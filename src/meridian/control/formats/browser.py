"""The connection page's `/sub/<token>/info` answer: subscription status as JSON.

The shape is Remnawave's subscription-info answer (what its subscription page read), so
pages and tools written against it keep working: the user's status, traffic, and expiry,
plus the same links the Base64 document lists.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from meridian.control.formats.entries import SubscriptionUser

# Remnawave stored "never expires" as the end of 2099; its answer still names that date.
NEVER_EXPIRES = datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)
_UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")


def subscription_info(user: SubscriptionUser, links: list[str], subscription_url: str, now: datetime) -> dict[str, Any]:
    expires = user.expires_at or NEVER_EXPIRES
    traffic = user.traffic
    info: dict[str, Any] = {
        "isFound": True,
        "user": {
            "shortUuid": user.short_id,
            "daysLeft": max(0, math.ceil((expires - now).total_seconds() / 86_400)),
            "trafficUsed": readable_bytes(traffic.total_bytes),
            "trafficLimit": readable_bytes(user.limit_bytes),
            "lifetimeTrafficUsed": readable_bytes(user.lifetime_used_bytes),
            "lifetimeTrafficUsedBytes": str(user.lifetime_used_bytes),
            "trafficLimitBytes": str(user.limit_bytes),
            "trafficUsedBytes": str(traffic.total_bytes),
            "trafficUploadBytes": str(traffic.upload_bytes),
            "trafficDownloadBytes": str(traffic.download_bytes),
            "trafficUnattributedBytes": str(traffic.unattributed_bytes),
            "username": user.username,
            "expiresAt": iso_timestamp(expires),
            "isActive": user.status == "active",
            "userStatus": user.status.upper(),
            "trafficLimitStrategy": user.limit_strategy.upper(),
        },
        "links": links,
        "ssConfLinks": {},
        "subscriptionUrl": subscription_url,
    }
    schedule = user.schedule
    # A named extension only when it says something: the frozen Remnawave answers have no such key.
    if schedule is not None and (schedule.activation_pending or schedule.renewal_enabled or schedule.next_reset_at):
        info["user"]["accountSchedule"] = schedule.to_data()
    return info


def not_found() -> dict[str, Any]:
    return {"isFound": False, "statusCode": 404, "message": "Resource not found"}


def readable_bytes(count: int) -> str:
    """Binary units with two decimals ("1.50 GiB"); zero stays "0", as an unlimited limit reads."""
    if count <= 0:
        return "0"
    exponent = min(int(math.log(count, 1024)), len(_UNITS) - 1)
    if exponent == 0:
        return f"{count} B"
    return f"{count / 1024**exponent:.2f} {_UNITS[exponent]}"


def iso_timestamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"
