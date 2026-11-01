"""Serve subscriptions from one consistent account, endpoint, settings, and device view."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from meridian.control.devices import DeviceLedger, HeaderReader, browser_request
from meridian.control.formats.entries import SubscriptionUser
from meridian.control.formats.selector import explain_format
from meridian.control.formats.stale_clients import stale_client
from meridian.control.models import ClientEndpoint, PrivacySettings, ServiceSettings, UserRecord
from meridian.control.store import Store
from meridian.control.subscriptions import (
    DEVICE_LIMIT_REMARKS,
    SubscriptionResponse,
    render_subscription,
    unknown_subscription,
)
from meridian.control.users import USER_SELECT, user_record
from meridian.core.languages import FleetLanguage, LanguageState, language_or_english, resolve_language
from meridian.core.notices import RenderedNotice


def serve_subscription(
    store: Store,
    devices: DeviceLedger,
    sub_token: str,
    suffix: str = "",
    *,
    read_settings: Callable[[], ServiceSettings],
    read_privacy: Callable[[], PrivacySettings],
    read_notice: Callable[[UserRecord], RenderedNotice | None],
    read_language: Callable[[], LanguageState],
    clock: Callable[[], float],
    render_page: Callable[[str], SubscriptionResponse],
    user_agent: str | None = None,
    accept: str | None = None,
    device_headers: HeaderReader | None = None,
) -> SubscriptionResponse:
    """Answer a subscription request: the document a suffix names, else the one the app reads."""
    document = explain_format(user_agent, accept, suffix).format
    if document == "browser" and not suffix:
        return render_page(sub_token)
    hwid_flags: dict[str, str] = {}
    with store.lock:
        row = store.db.execute(f"{USER_SELECT} WHERE u.sub_token = ?", (sub_token,)).fetchone()
        if row is None:
            return unknown_subscription()
        record = user_record(row)
        bodies = [item[0] for item in store.db.execute("SELECT body FROM endpoints").fetchall()]
        settings = read_settings()
        privacy = read_privacy()
        notice = read_notice(record)
        language = resolve_language(read_language().language, record.language)
        # The connection page's account check (`/info`) is never an app fetching its configs.
        if privacy.device_limits != "off" and document != "browser" and not browser_request(user_agent, accept):
            with store.transaction():
                hwid_flags = devices.fetch(record, privacy, device_headers if device_headers is not None else {})
    user = subscription_user(record, language)
    endpoints = [ClientEndpoint.model_validate_json(body) for body in bodies]
    return render_subscription(
        user,
        endpoints,
        document,
        profile_title=settings.profile_title,
        public_url=settings.public_url,
        update_interval_hours=settings.update_interval_hours,
        client_routing=settings.client_routing,
        owner_contact=settings.owner_contact,
        notice=notice,
        now=datetime.fromtimestamp(clock(), UTC),
        placeholder_remarks=DEVICE_LIMIT_REMARKS if "x-hwid-limit" in hwid_flags else None,
        extra_headers=hwid_flags,
        # Read from this request's header alone: never stored, logged, or kept beside a device.
        stale_client=None if document == "browser" else stale_client(user_agent),
    )


def subscription_user(record: UserRecord, language: FleetLanguage) -> SubscriptionUser:
    """One account projection for live delivery and preview; no private metadata travels."""
    return SubscriptionUser(
        username=record.username,
        uuid=record.uuid,
        short_id=record.sub_token,
        status=record.status,
        expires_at=datetime.fromisoformat(record.expire_at) if record.expire_at else None,
        used_bytes=record.used_traffic_bytes,
        period_traffic=record.period_traffic,
        limit_bytes=record.traffic_limit_bytes,
        lifetime_used_bytes=record.lifetime_traffic_bytes,
        limit_strategy=record.traffic_limit_strategy,
        language=language_or_english(language),
    )
