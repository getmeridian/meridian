"""Pure bulk before/after calculation over the shared account rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from meridian.control.account_lifecycle import merge_user_update
from meridian.control.models import UserRecord, UserUpdate
from meridian.control.user_policy import enforced_status
from meridian.core.account_schedule import (
    activation_expiry,
    calculate_schedule,
    calendar_expiry,
    period_start,
    timestamp,
)
from meridian.core.people_bulk import BULK_WRITABLE_FIELDS, BulkAction, BulkFields
from meridian.core.traffic import PeriodTraffic


@dataclass
class BulkEffect:
    user: UserRecord
    reset: bool = False
    requested: UserRecord | None = None


def project_effect(user: UserRecord, now: str, *, reset: bool = False) -> BulkEffect:
    """The same schedule and status calculators used by transactional enforcement."""
    moment = datetime.fromisoformat(now)
    schedule = calculate_schedule(user, moment)
    period = period_start(user.traffic_limit_strategy, moment, user.traffic_reset_anchor_day)
    reset = (
        reset
        or bool(schedule.renewals_due)
        or bool(
            period is not None
            and (user.traffic_reset_at is None or datetime.fromisoformat(user.traffic_reset_at) < period)
        )
    )
    values = user.model_dump()
    values.update(expire_at=schedule.expire_at, renewals_completed=schedule.renewals_completed)
    if schedule.renewals_due or schedule.expire_at != user.expire_at:
        values["renewal_anchor_at"] = (
            user.renewal_anchor_at or timestamp(activation_expiry(user)) if user.renewal_enabled else None
        )
    if reset:
        values.update(traffic_reset_at=now, used_traffic_bytes=0, period_traffic=PeriodTraffic())
    projected = UserRecord.model_validate(values)
    projected = UserRecord.model_validate({**projected.model_dump(), "status": enforced_status(projected, now)})
    return BulkEffect(projected, reset)


def prepare_effect(user: UserRecord, action: BulkAction, now: str) -> BulkEffect:
    changes = action.model_dump(include=action.model_fields_set - {"action", "days", "months"})
    if action.action == "extend":
        moment = datetime.fromisoformat(now)
        if user.expire_at is None and user.activation_duration_days is not None and user.first_traffic_at is None:
            days = (
                action.days if action.days is not None else (calendar_expiry(moment, action.months or 1) - moment).days
            )
            changes = {"activation_duration_days": user.activation_duration_days + days}
        else:
            start = max(moment, datetime.fromisoformat(user.expire_at)) if user.expire_at else moment
            expiry = (
                start + timedelta(days=action.days)
                if action.days is not None
                else calendar_expiry(start, action.months or 1)
            )
            changes = {"expire_at": timestamp(expiry)}
    elif action.action in ("enable", "disable"):
        changes = {"status": "active" if action.action == "enable" else "disabled"}
    elif action.action == "renewal":
        intervals = action.model_fields_set & {"renewal_interval_days", "renewal_interval_months"}
        if intervals and all(getattr(action, field) is None for field in intervals):
            changes.update(renewal_interval_days=None, renewal_interval_months=None, renewal_limit=None)
    merged = merge_user_update(user, UserUpdate.model_validate(changes), now)
    effect = project_effect(merged, now, reset=action.action == "reset-traffic")
    effect.requested = merged
    return effect


def prepare_undo(user: UserRecord, before: BulkFields, action: BulkAction, now: str) -> BulkEffect:
    values = {field: getattr(before, field) for field in BULK_WRITABLE_FIELDS}
    # Only enable/disable changes the operator's status choice; other states belong to enforcement.
    values["status"] = (
        ("disabled" if before.status == "disabled" else "active")
        if action.action in ("enable", "disable")
        else user.status
    )
    requested = UserRecord.model_validate({**user.model_dump(), **values})
    effect = project_effect(requested, now)
    effect.requested = requested
    return effect
