"""How a stored user row becomes a `UserRecord`; the limits it obeys live in `user_policy.py`."""

from __future__ import annotations

from typing import Any

from meridian.control.models import UserRecord

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
    " u.traffic_offset_bytes, t.last_seen_at, t.first_traffic_at, t.last_traffic_node"
    " FROM users u LEFT JOIN traffic_totals t ON t.user_id = u.user_id"
)


def user_record(row: Any) -> UserRecord:
    *stored, lifetime, offset, online_at, first_traffic_at, last_traffic_node = tuple(row)
    return UserRecord.model_validate(
        {
            **dict(zip(USER_FIELDS, stored, strict=True)),
            "lifetime_traffic_bytes": lifetime,
            "used_traffic_bytes": max(0, lifetime - offset),
            "online_at": online_at,
            "first_traffic_at": first_traffic_at,
            "last_traffic_node": last_traffic_node,
        }
    )
