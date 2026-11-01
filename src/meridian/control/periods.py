"""Pure UTC traffic period boundaries for enforcement and subscription delivery."""

from __future__ import annotations

from datetime import datetime

from meridian.control.models import LimitStrategy
from meridian.core.account_schedule import next_period_start as schedule_next_period
from meridian.core.account_schedule import period_start as schedule_period_start


def period_start(strategy: LimitStrategy, now: datetime, anchor_day: int | None = None) -> datetime | None:
    """The start of the traffic period *now* falls in (UTC), or None when traffic never resets."""
    return schedule_period_start(strategy, now, anchor_day)


def next_period_start(strategy: LimitStrategy, now: datetime, anchor_day: int | None = None) -> datetime | None:
    """The first instant of the next UTC period; weeks start on Monday."""
    return schedule_next_period(strategy, now, anchor_day)
