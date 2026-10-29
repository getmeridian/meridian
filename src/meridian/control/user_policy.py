"""UTC traffic periods, imported timestamps, and enforcement of operator-owned user limits."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from meridian.control.models import LimitStrategy, UserImport, UserRecord, UserStatus


def enforced_status(user: UserRecord, now: str) -> UserStatus:
    """The status enforcement gives a user; `disabled` is the operator's and never changes."""
    if user.status == "disabled":
        return "disabled"
    if _expired(user, now):
        return "expired"
    over = user.traffic_limit_bytes > 0 and user.used_traffic_bytes >= user.traffic_limit_bytes
    return "limited" if over else "active"


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


def whole_seconds(timestamp: str | None) -> str | None:
    """A protocol timestamp without its fraction, as the service writes every time."""
    return f"{timestamp[:19]}Z" if timestamp else None


def period_began(item: UserImport, now: str) -> str | None:
    """When an imported user's current traffic period began.

    The source's last reset is kept only inside the current period: an older one would
    make the next enforcement reset the imported use at once. Otherwise the period runs
    on from the import with the use the source counted, until its next boundary.
    """
    reset_at = whole_seconds(item.traffic_reset_at)
    start = period_start(item.traffic_limit_strategy, datetime.fromisoformat(now))
    if start is None:
        return reset_at
    if reset_at is not None and datetime.fromisoformat(reset_at) >= start:
        return reset_at
    return now


def _expired(user: UserRecord, now: str) -> bool:
    # Parse, never compare strings: a fractional "…:00.5Z" sorts before "…:00Z".
    return user.expire_at is not None and datetime.fromisoformat(user.expire_at) <= datetime.fromisoformat(now)
