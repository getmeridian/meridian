"""Typed account schedules and pure UTC dates shared by every account surface."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Protocol

from pydantic import Field, model_validator

from meridian.core.agent_types import MAX_SAFE_INTEGER, Timestamp
from meridian.core.calendar import month_date, monthly_period, shift_month
from meridian.core.models import CoreModel

AnchorDay = Annotated[int, Field(ge=1, le=31)]
DurationDays = Annotated[int, Field(ge=1, le=3650)]
RenewalMonths = Annotated[int, Field(ge=1, le=120)]
RenewalLimit = Annotated[int, Field(ge=1, le=10_000)]
FINITE_EXPIRY_YEAR = 2099


class AccountLifecycleSettings(CoreModel):
    """Operator-owned schedule fields; null clears a choice and omission preserves it."""

    traffic_reset_anchor_day: AnchorDay | None = Field(
        default=None, description="UTC month day; null uses creation day."
    )
    activation_duration_days: DurationDays | None = Field(
        default=None, description="Whole UTC days from first traffic."
    )
    renewal_interval_days: DurationDays | None = None
    renewal_interval_months: RenewalMonths | None = None
    renewal_limit: RenewalLimit | None = Field(
        default=None, description="Renewals in this series; null means unlimited."
    )

    @model_validator(mode="after")
    def one_renewal_interval(self) -> AccountLifecycleSettings:
        if self.renewal_interval_days is not None and self.renewal_interval_months is not None:
            raise ValueError("Choose a renewal interval in days or calendar months.")
        return self

    @property
    def renewal_enabled(self) -> bool:
        return self.renewal_interval_days is not None or self.renewal_interval_months is not None


LIFECYCLE_FIELDS = tuple(AccountLifecycleSettings.model_fields)


class AccountLifecycleState(AccountLifecycleSettings):
    renewals_completed: int = Field(default=0, ge=0, le=MAX_SAFE_INTEGER)
    renewal_anchor_at: Timestamp | None = Field(
        default=None, description="Original expiry of the current renewal series."
    )


class ScheduleSource(Protocol):
    created_at: str
    expire_at: str | None
    first_traffic_at: str | None

    @property
    def traffic_limit_strategy(self) -> str: ...

    traffic_reset_anchor_day: int | None
    activation_duration_days: int | None
    renewal_interval_days: int | None
    renewal_interval_months: int | None
    renewal_limit: int | None
    renewals_completed: int
    renewal_anchor_at: str | None


class AccountSchedulePreview(CoreModel):
    """Effective dates after catch-up; calculating them never writes or activates an account."""

    activation_pending: bool
    activation_duration_days: DurationDays | None
    expire_at: Timestamp | None
    next_reset_at: Timestamp | None
    renewal_enabled: bool
    next_renewal_at: Timestamp | None
    renewals_left: int | None = Field(ge=0, description="Null for unlimited renewals or when renewal is off.")
    renewals_due: int = Field(ge=0, description="Missed renewals applied together with one traffic reset.")
    renewals_completed: int = Field(ge=0)


def timestamp(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    utc = moment.astimezone(UTC)
    return utc.isoformat().replace("+00:00", "Z") if utc.microsecond else utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def calendar_expiry(base: datetime, months: int) -> datetime:
    """Shift from the original UTC base, preserving its time and clamping its day."""
    base = base.astimezone(UTC)
    year, month = shift_month(base.year, base.month, months)
    day = month_date(year, month, base.day)
    return base.replace(year=day.year, month=day.month, day=day.day)


def period_start(strategy: str, now: datetime, anchor_day: int | None = None) -> datetime | None:
    """Current UTC traffic boundary; rolling months always retain the chosen anchor."""
    midnight = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if strategy == "no_reset":
        return None
    if strategy == "day":
        return midnight
    if strategy == "week":
        return midnight - timedelta(days=midnight.weekday())
    if strategy == "month":
        return midnight.replace(day=1)
    if strategy == "month_rolling" and anchor_day is not None and 1 <= anchor_day <= 31:
        return monthly_period(anchor_day, now)[0]
    raise ValueError("A rolling monthly reset needs an anchor day from 1 to 31.")


def next_period_start(strategy: str, now: datetime, anchor_day: int | None = None) -> datetime | None:
    start = period_start(strategy, now, anchor_day)
    if start is None:
        return None
    if strategy == "day":
        return start + timedelta(days=1)
    if strategy == "week":
        return start + timedelta(days=7)
    if strategy == "month_rolling":
        assert anchor_day is not None
        return monthly_period(anchor_day, now)[1]
    return calendar_expiry(start, 1)


def activation_expiry(source: ScheduleSource) -> datetime | None:
    """The stored expiry, or the first-use expiry once accepted traffic establishes it."""
    expiry = datetime.fromisoformat(source.expire_at).astimezone(UTC) if source.expire_at else None
    duration = source.activation_duration_days
    if expiry is None and duration is not None and source.first_traffic_at is not None:
        expiry = datetime.fromisoformat(source.first_traffic_at).astimezone(UTC) + timedelta(days=duration)
        # Writes refuse durations that would cross the finite range; a late first use clamps
        # instead of raising, so reading an account can never fail.
        expiry = min(expiry, datetime(FINITE_EXPIRY_YEAR, 1, 1, tzinfo=UTC) - timedelta(seconds=1))
    return expiry


def calculate_schedule(source: ScheduleSource, now: datetime) -> AccountSchedulePreview:
    """Project activation, catch-up, and future boundaries from one validated account."""
    moment = now.astimezone(UTC)
    expiry = activation_expiry(source)
    duration = source.activation_duration_days
    enabled = source.renewal_interval_days is not None or source.renewal_interval_months is not None
    completed = source.renewals_completed
    left = max(0, source.renewal_limit - completed) if enabled and source.renewal_limit is not None else None
    due = 0
    next_renewal = None
    if enabled and expiry is not None:
        anchor = (
            datetime.fromisoformat(source.renewal_anchor_at).astimezone(UTC) if source.renewal_anchor_at else expiry
        )
        due = _renewals_due(source, anchor, expiry, moment)
        if left is not None:
            due = min(due, left)
        # A finite expiry must never become the legacy no-expiry sentinel.
        due = min(due, max(0, _last_supported_renewal(source, anchor) - completed))
        completed += due
        if due:
            expiry = renewed_expiry(source, anchor, completed)
        if left is not None:
            left -= due
        if (left is None or left > 0) and completed < _last_supported_renewal(source, anchor):
            next_renewal = expiry
    anchor_day = source.traffic_reset_anchor_day or datetime.fromisoformat(source.created_at).astimezone(UTC).day
    periodic = next_period_start(source.traffic_limit_strategy, moment, anchor_day)
    refills = [date for date in (periodic, next_renewal) if date is not None]
    return AccountSchedulePreview(
        activation_pending=expiry is None and duration is not None,
        activation_duration_days=duration,
        expire_at=timestamp(expiry),
        next_reset_at=timestamp(min(refills)) if refills else None,
        renewal_enabled=enabled,
        next_renewal_at=timestamp(next_renewal),
        renewals_left=left,
        renewals_due=due,
        renewals_completed=completed,
    )


def renewed_expiry(source: ScheduleSource, anchor: datetime, index: int) -> datetime:
    if source.renewal_interval_days is not None:
        return anchor + timedelta(days=source.renewal_interval_days * index)
    assert source.renewal_interval_months is not None
    return calendar_expiry(anchor, source.renewal_interval_months * index)


def _last_supported_renewal(source: ScheduleSource, anchor: datetime) -> int:
    if source.renewal_interval_days is not None:
        last = datetime(FINITE_EXPIRY_YEAR, 1, 1, tzinfo=UTC) - timedelta(seconds=1)
        return max(0, (last - anchor).days // source.renewal_interval_days)
    assert source.renewal_interval_months is not None
    months = (FINITE_EXPIRY_YEAR - 1 - anchor.year) * 12 + 12 - anchor.month
    return max(0, months // source.renewal_interval_months)


def _renewals_due(source: ScheduleSource, anchor: datetime, expiry: datetime, now: datetime) -> int:
    if now < expiry:
        return 0
    if source.renewal_interval_days is not None:
        return (now - expiry).days // source.renewal_interval_days + 1
    assert source.renewal_interval_months is not None
    elapsed = (now.year - anchor.year) * 12 + now.month - anchor.month
    index = max(0, elapsed // source.renewal_interval_months)
    if renewed_expiry(source, anchor, index) > now:
        index -= 1
    return max(0, index - source.renewals_completed + 1)
