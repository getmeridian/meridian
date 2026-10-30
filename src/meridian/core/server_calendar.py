"""Pure UTC calendar boundaries and anchored server payment repeats."""

from __future__ import annotations

from calendar import monthrange
from datetime import UTC, date, datetime

from meridian.core.server_allowances import PaymentRepeat


def _month_date(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, monthrange(year, month)[1]))


def _shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
    shifted_year, shifted_month = divmod(year * 12 + month - 1 + offset, 12)
    return shifted_year, shifted_month + 1


def server_period(reset_day: int, now: datetime) -> tuple[datetime, datetime]:
    """The UTC month containing now, clamping the reset to the last day of a short month."""
    moment = now.astimezone(UTC)
    start_day = _month_date(moment.year, moment.month, reset_day)
    start = datetime(start_day.year, start_day.month, start_day.day, tzinfo=UTC)
    if moment < start:
        year, month = _shift_month(moment.year, moment.month, -1)
        start_day = _month_date(year, month, reset_day)
        start = datetime(start_day.year, start_day.month, start_day.day, tzinfo=UTC)
    year, month = _shift_month(start.year, start.month, 1)
    end_day = _month_date(year, month, reset_day)
    return start, datetime(end_day.year, end_day.month, end_day.day, tzinfo=UTC)


def next_payment_date(anchor: date | None, repeat: PaymentRepeat | None, today: date) -> date | None:
    """The first repeat on or after today; preserve the original day after a short month or leap year."""
    if anchor is None or repeat is None or anchor >= today:
        return anchor
    if repeat == "month":
        months = (today.year - anchor.year) * 12 + today.month - anchor.month
        year, month = _shift_month(anchor.year, anchor.month, months)
        candidate = _month_date(year, month, anchor.day)
        if candidate < today:
            year, month = _shift_month(year, month, 1)
            candidate = _month_date(year, month, anchor.day)
        return candidate
    candidate = _month_date(today.year, anchor.month, anchor.day)
    return _month_date(today.year + 1, anchor.month, anchor.day) if candidate < today else candidate
