"""Pure UTC calendar boundaries and anchored server payment repeats."""

from __future__ import annotations

from datetime import UTC, date, datetime

from meridian.core.calendar import month_date, shift_month
from meridian.core.server_allowances import PaymentRepeat


def server_period(reset_day: int, now: datetime) -> tuple[datetime, datetime]:
    """The UTC month containing now, clamping the reset to the last day of a short month."""
    moment = now.astimezone(UTC)
    start_day = month_date(moment.year, moment.month, reset_day)
    start = datetime(start_day.year, start_day.month, start_day.day, tzinfo=UTC)
    if moment < start:
        year, month = shift_month(moment.year, moment.month, -1)
        start_day = month_date(year, month, reset_day)
        start = datetime(start_day.year, start_day.month, start_day.day, tzinfo=UTC)
    year, month = shift_month(start.year, start.month, 1)
    end_day = month_date(year, month, reset_day)
    return start, datetime(end_day.year, end_day.month, end_day.day, tzinfo=UTC)


def next_payment_date(anchor: date | None, repeat: PaymentRepeat | None, today: date) -> date | None:
    """The first repeat on or after today; preserve the original day after a short month or leap year."""
    if anchor is None or repeat is None or anchor >= today:
        return anchor
    if repeat == "month":
        months = (today.year - anchor.year) * 12 + today.month - anchor.month
        year, month = shift_month(anchor.year, anchor.month, months)
        candidate = month_date(year, month, anchor.day)
        if candidate < today:
            year, month = shift_month(year, month, 1)
            candidate = month_date(year, month, anchor.day)
        return candidate
    candidate = month_date(today.year, anchor.month, anchor.day)
    return month_date(today.year + 1, anchor.month, anchor.day) if candidate < today else candidate
