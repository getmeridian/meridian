"""How a stored user row becomes a `UserRecord`; the limits it obeys live in `user_policy.py`."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from meridian.control.models import UserRecord
from meridian.core.attention import AttentionThresholds, person_reasons
from meridian.core.person_activity import is_never_used
from meridian.core.traffic import period_usage, quota_percent

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
    "note",
    "tag",
    "language",
)
# Lifetime includes the ledger and imported use; the offset leaves the current period's growth.
USER_SELECT = (
    "SELECT "
    + ", ".join(f"u.{name}" for name in USER_FIELDS)
    + ", COALESCE(t.uplink_bytes + t.downlink_bytes, 0) + u.traffic_carried_bytes AS lifetime,"
    " COALESCE(t.uplink_bytes, 0), COALESCE(t.downlink_bytes, 0),"
    " u.traffic_upload_offset_bytes, u.traffic_download_offset_bytes, u.traffic_period_carried_bytes,"
    " t.last_seen_at, t.first_traffic_at, t.last_traffic_node"
    " FROM users u LEFT JOIN traffic_totals t ON t.user_id = u.user_id"
)


def user_record(row: Any, *, now: datetime) -> UserRecord:
    *stored, lifetime, up, down, up_offset, down_offset, carried, online_at, first_traffic_at, last_traffic_node = (
        tuple(row)
    )
    usage = period_usage(up, down, upload_offset=up_offset, download_offset=down_offset, carried_bytes=carried)
    record = UserRecord.model_validate(
        {
            **dict(zip(USER_FIELDS, stored, strict=True)),
            "lifetime_traffic_bytes": lifetime,
            "used_traffic_bytes": usage.total_bytes,
            "period_traffic": usage,
            "online_at": online_at,
            "first_traffic_at": first_traffic_at,
            "last_traffic_node": last_traffic_node,
        }
    )
    return UserRecord.model_validate(
        {
            **record.to_data(),
            "never_used": is_never_used(record),
            "quota_used_percent": quota_percent(record.used_traffic_bytes, record.traffic_limit_bytes),
            "attention_reasons": person_reasons(record, AttentionThresholds(), now),
        }
    )
