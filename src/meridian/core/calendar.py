"""Pure calendar-month shifts shared by account renewal and server billing."""

from calendar import monthrange
from datetime import date


def month_date(year: int, month: int, day: int) -> date:
    """Keep the requested day, clamping it to the last day of a short month."""
    return date(year, month, min(day, monthrange(year, month)[1]))


def shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
    """Shift a year/month pair by a calendar-month offset."""
    shifted_year, shifted_month = divmod(year * 12 + month - 1 + offset, 12)
    return shifted_year, shifted_month + 1
