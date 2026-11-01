"""Pure UTC calendar boundaries and anchored server payment repeats."""

from __future__ import annotations

from datetime import date, datetime

from meridian.core.calendar import month_date, monthly_period, shift_month
from meridian.core.server_allowances import PaymentRepeat


def server_period(reset_day: int, now: datetime) -> tuple[datetime, datetime]:
    """The UTC month containing now, clamping the reset to the last day of a short month."""
    return monthly_period(reset_day, now)


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
