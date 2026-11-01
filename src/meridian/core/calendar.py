"""Pure calendar-month shifts shared by account renewal and server billing."""

from calendar import monthrange
from datetime import UTC, date, datetime


def month_date(year: int, month: int, day: int) -> date:
    """Keep the requested day, clamping it to the last day of a short month."""
    return date(year, month, min(day, monthrange(year, month)[1]))


def shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
    """Shift a year/month pair by a calendar-month offset."""
    shifted_year, shifted_month = divmod(year * 12 + month - 1 + offset, 12)
    return shifted_year, shifted_month + 1


def monthly_period(anchor_day: int, now: datetime) -> tuple[datetime, datetime]:
    """UTC month boundaries retaining an anchor through short months and leap years."""
    moment = now.astimezone(UTC)
    start_day = month_date(moment.year, moment.month, anchor_day)
    start = datetime(start_day.year, start_day.month, start_day.day, tzinfo=UTC)
    if moment < start:
        year, month = shift_month(moment.year, moment.month, -1)
        start_day = month_date(year, month, anchor_day)
        start = datetime(start_day.year, start_day.month, start_day.day, tzinfo=UTC)
    year, month = shift_month(start.year, start.month, 1)
    end_day = month_date(year, month, anchor_day)
    return start, datetime(end_day.year, end_day.month, end_day.day, tzinfo=UTC)
