"""User storage projection and UTC quota/expiry normalization."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from meridian.control.models import LimitStrategy, UserImport, UserRecord, UserStatus

USER_FIELDS = (
    "username",
    "user_id",
    "uuid",
    "sub_token",
    "status",
    "created_at",
    "expire_at",
    "traffic_limit_bytes",
    "traffic_limit_strategy",
    "traffic_reset_at",
    "device_limit",
)
# Lifetime includes the ledger and imported use; the offset leaves the current period's growth.
USER_SELECT = (
    "SELECT "
    + ", ".join(f"u.{name}" for name in USER_FIELDS)
    + ", COALESCE(t.uplink_bytes + t.downlink_bytes, 0) + u.traffic_carried_bytes AS lifetime,"
    " u.traffic_offset_bytes, t.last_seen_at"
    " FROM users u LEFT JOIN traffic_totals t ON t.user_id = u.user_id"
)


def user_record(row: Any) -> UserRecord:
    *stored, lifetime, offset, online_at = tuple(row)
    return UserRecord.model_validate(
        {
            **dict(zip(USER_FIELDS, stored, strict=True)),
            "lifetime_traffic_bytes": lifetime,
            "used_traffic_bytes": max(0, lifetime - offset),
            "online_at": online_at,
        }
    )


def enforced_status(user: UserRecord, now: str) -> UserStatus:
    """Disabled is the operator's; expiry and quota determine every other status."""
    if user.status == "disabled":
        return "disabled"
    if user.expire_at is not None and datetime.fromisoformat(user.expire_at) <= datetime.fromisoformat(now):
        return "expired"
    return (
        "limited" if user.traffic_limit_bytes > 0 and user.used_traffic_bytes >= user.traffic_limit_bytes else "active"
    )


def period_start(strategy: LimitStrategy, now: datetime) -> datetime | None:
    """The current UTC traffic period (weeks start Monday); None when it never resets."""
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
    """Keep an imported reset only in this period, so enforcement cannot wipe the imported use."""
    reset_at = whole_seconds(item.traffic_reset_at)
    start = period_start(item.traffic_limit_strategy, datetime.fromisoformat(now))
    if start is None:
        return reset_at
    if reset_at is not None and datetime.fromisoformat(reset_at) >= start:
        return reset_at
    return now
