"""Pure UTC traffic period boundaries for enforcement and subscription delivery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from meridian.control.models import LimitStrategy


def period_start(strategy: LimitStrategy, now: datetime) -> datetime | None:
    """The start of the traffic period *now* falls in (UTC), or None when traffic never resets."""
    midnight = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if strategy == "day":
        return midnight
    if strategy == "week":
        return midnight - timedelta(days=midnight.weekday())
    if strategy == "month":
        return midnight.replace(day=1)
    return None


def next_period_start(strategy: LimitStrategy, now: datetime) -> datetime | None:
    """The first instant of the next UTC period; weeks start on Monday."""
    start = period_start(strategy, now)
    if start is None:
        return None
    if strategy == "day":
        return start + timedelta(days=1)
    if strategy == "week":
        return start + timedelta(days=7)
    return start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
