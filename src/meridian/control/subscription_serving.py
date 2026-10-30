"""Serve subscriptions from one consistent account, endpoint, settings, and device view."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from meridian.control.devices import DeviceLedger, HeaderReader, browser_request
from meridian.control.formats.entries import SubscriptionUser
from meridian.control.formats.selector import SUFFIX_FORMATS, select_format
from meridian.control.models import ClientEndpoint, PrivacySettings, ServiceSettings
from meridian.control.store import Store
from meridian.control.subscriptions import (
    DEVICE_LIMIT_REMARKS,
    SubscriptionResponse,
    render_subscription,
    unknown_subscription,
)
from meridian.control.users import USER_SELECT, user_record


def serve_subscription(
    store: Store,
    devices: DeviceLedger,
    sub_token: str,
    suffix: str = "",
    *,
    read_settings: Callable[[], ServiceSettings],
    read_privacy: Callable[[], PrivacySettings],
    clock: Callable[[], float],
    user_agent: str | None = None,
    accept: str | None = None,
    device_headers: HeaderReader | None = None,
) -> SubscriptionResponse:
    """Answer a subscription request: the document a suffix names, else the one the app reads."""
    document = SUFFIX_FORMATS[suffix] if suffix else select_format(user_agent, accept)
    hwid_flags: dict[str, str] = {}
    with store.lock:
        row = store.db.execute(f"{USER_SELECT} WHERE u.sub_token = ?", (sub_token,)).fetchone()
        if row is None:
            return unknown_subscription()
        record = user_record(row)
        bodies = [item[0] for item in store.db.execute("SELECT body FROM endpoints").fetchall()]
        settings = read_settings()
        privacy = read_privacy()
        # The connection page's account check (`/info`) is never an app fetching its configs.
        if privacy.device_limits != "off" and document != "browser" and not browser_request(user_agent, accept):
            with store.transaction():
                hwid_flags = devices.fetch(record, privacy, device_headers if device_headers is not None else {})
    user = SubscriptionUser(
        username=record.username,
        uuid=record.uuid,
        short_id=record.sub_token,
        status=record.status,
        expires_at=datetime.fromisoformat(record.expire_at) if record.expire_at else None,
        used_bytes=record.used_traffic_bytes,
        limit_bytes=record.traffic_limit_bytes,
        lifetime_used_bytes=record.lifetime_traffic_bytes,
        limit_strategy=record.traffic_limit_strategy,
    )
    endpoints = [ClientEndpoint.model_validate_json(body) for body in bodies]
    return render_subscription(
        user,
        endpoints,
        document,
        profile_title=settings.profile_title,
        public_url=settings.public_url,
        update_interval_hours=settings.update_interval_hours,
        client_routing=settings.client_routing,
        now=datetime.fromtimestamp(clock(), UTC),
        placeholder_remarks=DEVICE_LIMIT_REMARKS if "x-hwid-limit" in hwid_flags else None,
        extra_headers=hwid_flags,
    )
