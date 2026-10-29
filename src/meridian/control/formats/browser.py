"""What a browser that opens the subscription URL gets: the subscription's status as JSON.

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
    return {
        "isFound": True,
        "user": {
            "shortUuid": user.short_id,
            "daysLeft": max(0, math.ceil((expires - now).total_seconds() / 86_400)),
            "trafficUsed": readable_bytes(user.used_bytes),
            "trafficLimit": readable_bytes(user.limit_bytes),
            "lifetimeTrafficUsed": readable_bytes(user.lifetime_used_bytes),
            "lifetimeTrafficUsedBytes": str(user.lifetime_used_bytes),
            "trafficLimitBytes": str(user.limit_bytes),
            "trafficUsedBytes": str(user.used_bytes),
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
