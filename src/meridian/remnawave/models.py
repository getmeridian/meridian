"""Shared models: a Remnawave user `meridian migrate` reads, and the node, host, and document shapes
the control service's projections still use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class User:
    """A Remnawave user (= Meridian client).

    ``id`` is Remnawave 3.x's numeric user identity, kept as a string so it
    can travel through bindings and URLs unchanged.
    """

    id: str = ""
    short_uuid: str = ""
    username: str = ""
    vless_uuid: str = ""  # VLESS protocol UUID (used by Xray, differs from account UUID)
    status: str = ""  # "ACTIVE", "DISABLED", "LIMITED", "EXPIRED"
    used_traffic_bytes: int = 0  # this traffic period's use
    lifetime_used_traffic_bytes: int = 0
    last_traffic_reset_at: str = ""
    traffic_limit_bytes: int = 0
    traffic_limit_strategy: str = ""  # "NO_RESET", "DAY", "WEEK", "MONTH", "MONTH_ROLLING"
    expire_at: str = ""
    created_at: str = ""
    online_at: str = ""
    sub_revoked_at: str = ""
    subscription_url: str = ""
    active_internal_squad_uuids: list[str] = field(default_factory=list)
    external_squad_uuid: str = ""
    description: str = ""
    # Panel-only settings `meridian migrate` reports as not carried over.
    tag: str = ""
    email: str = ""
    telegram_id: str = ""
    # The control projection also uses this cap; migrate reports the panel's instead of importing it.
    hwid_device_limit: int | None = None
    _raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class Node:
    """A Remnawave proxy node."""

    uuid: str = ""
    name: str = ""
    address: str = ""
    port: int = 0
    is_connected: bool = False
    is_disabled: bool = False
    # Set only by the control service: the agent reports but its last apply failed.
    apply_failed: bool = False
    xray_version: str = ""
    traffic_used: int = 0
    country_code: str = ""
    active_config_profile_uuid: str = ""
    active_inbound_uuids: list[str] = field(default_factory=list)
    _raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class Host:
    """A Remnawave host entry (direct address or relay)."""

    uuid: str = ""
    remark: str = ""
    address: str = ""
    port: int = 0
    sni: str = ""
    host: str = ""
    path: str = ""
    alpn: str = ""
    fingerprint: str = ""
    security_layer: str = "DEFAULT"
    config_profile_uuid: str = ""
    inbound_uuid: str = ""
    is_disabled: bool = False
    tags: list[str] = field(default_factory=list)
    is_hidden: bool = False
    xray_json_template_uuid: str = ""
    exclude_from_subscription_types: list[str] = field(default_factory=list)
    view_position: int = 0
    _raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class SubscriptionDocument:
    """Canonical subscription content returned by Remnawave."""

    url: str
    content: str
    client_type: str = ""
    content_type: str = ""


def parse_user(data: Any) -> User:
    """Parse a raw API user payload into the stable model."""
    raw = data if isinstance(data, dict) else {}
    traffic = raw.get("userTraffic")
    traffic = traffic if isinstance(traffic, dict) else {}
    squads = raw.get("activeInternalSquads")
    squads = squads if isinstance(squads, list) else []
    return User(
        id=str(raw.get("id") if raw.get("id") is not None else ""),
        short_uuid=str(raw.get("shortUuid") or ""),
        username=str(raw.get("username") or ""),
        vless_uuid=str(raw.get("vlessUuid") or ""),
        status=str(raw.get("status") or ""),
        used_traffic_bytes=int(traffic.get("usedTrafficBytes") or 0),
        lifetime_used_traffic_bytes=int(traffic.get("lifetimeUsedTrafficBytes") or 0),
        last_traffic_reset_at=str(raw.get("lastTrafficResetAt") or ""),
        traffic_limit_bytes=int(raw.get("trafficLimitBytes") or 0),
        traffic_limit_strategy=str(raw.get("trafficLimitStrategy") or ""),
        expire_at=str(raw.get("expireAt") or ""),
        created_at=str(raw.get("createdAt") or ""),
        online_at=str(traffic.get("onlineAt") or ""),
        sub_revoked_at=str(raw.get("subRevokedAt") or ""),
        subscription_url=str(raw.get("subscriptionUrl") or ""),
        active_internal_squad_uuids=[str(item.get("uuid") if isinstance(item, dict) else item) for item in squads],
        external_squad_uuid=str(raw.get("externalSquadUuid") or ""),
        description=str(raw.get("description") or ""),
        tag=str(raw.get("tag") or ""),
        email=str(raw.get("email") or ""),
        telegram_id=str(raw.get("telegramId") or ""),
        hwid_device_limit=raw["hwidDeviceLimit"] if isinstance(raw.get("hwidDeviceLimit"), int) else None,
        _raw=raw,
    )


def response_items(data: Any, *keys: str) -> list[dict[str, Any]]:
    """Extract a typed-object list from known Remnawave response wrappers."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []
