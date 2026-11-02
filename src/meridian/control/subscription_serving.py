"""Serve subscriptions from one consistent account, endpoint, settings, and device view."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import ValidationError

from meridian.control.access_memberships import eligible_endpoints
from meridian.control.delivery_preview import preview_delivery_policy
from meridian.control.device_slots import read_device_slots
from meridian.control.devices import DeviceLedger, HeaderReader, browser_request
from meridian.control.formats.entries import SubscriptionUser
from meridian.control.formats.response_rules import evaluate_response_rules
from meridian.control.formats.stale_clients import stale_client
from meridian.control.models import ClientEndpoint, PrivacySettings, ServiceSettings, UserRecord
from meridian.control.store import Store
from meridian.control.subscriptions import (
    DEVICE_LIMIT_REMARKS,
    SubscriptionResponse,
    apply_response_headers,
    render_subscription,
    unknown_subscription,
)
from meridian.control.users import USER_SELECT, user_record
from meridian.core.delivery_preview import subscriber_delivery_capabilities
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
    hwid_flags: dict[str, str] = {}
    with store.lock:
        settings = read_settings()
        try:
            decision = evaluate_response_rules(settings, user_agent=user_agent, accept=accept, suffix=suffix)
        except ValidationError:
            return unknown_subscription()
        document = decision.format
        if document is None:
            return unknown_subscription()
        # A refused app never reaches token lookup; valid and unknown tokens take the same path.
        row = store.db.execute(f"{USER_SELECT} WHERE u.sub_token = ?", (sub_token,)).fetchone()
        if row is None:
            return unknown_subscription()
        record = user_record(row, now=datetime.fromtimestamp(clock(), UTC))
        if record.delivery_policy is not None or settings.profile_policies:
            decision = evaluate_response_rules(
                settings,
                user_agent=user_agent,
                accept=accept,
                suffix=suffix,
                profiles=record.profiles,
                person_override=record.delivery_policy,
            )
        delivery_policy = decision.policy
        assert delivery_policy is not None
        bodies = [item[0] for item in store.db.execute("SELECT body FROM endpoints").fetchall()]
        privacy = read_privacy()
        slots = read_device_slots(store, record, privacy) if document == "browser" else None
        notice = read_notice(record)
        language = resolve_language(read_language().language, record.language)
        # The connection page's account check (`/info`) is never an app fetching its configs.
        if privacy.device_limits != "off" and document != "browser" and not browser_request(user_agent, accept):
            with store.transaction():
                hwid_flags = devices.fetch(record, privacy, device_headers if device_headers is not None else {})
    if decision.response_kind == "page":
        return apply_response_headers(render_page(sub_token), decision.headers)
    user = subscription_user(record, language)
    endpoints = eligible_endpoints(
        record, [ClientEndpoint.model_validate_json(body) for body in bodies], settings.access_profiles
    )
    response = render_subscription(
        user,
        endpoints,
        document,
        profile_title=settings.profile_title,
        public_url=settings.public_url,
        update_interval_hours=settings.update_interval_hours,
        client_routing=settings.client_routing,
        delivery_policy=delivery_policy,
        delivery_capabilities=subscriber_delivery_capabilities(preview_delivery_policy(delivery_policy, endpoints))
        if document == "browser"
        and (
            settings.default_policy is not None
            or settings.profile_policies
            or record.delivery_policy is not None
            or delivery_policy.source == "response_rule"
        )
        else None,
        owner_contact=settings.owner_contact,
        notice=notice,
        device_slots=slots,
        now=datetime.fromtimestamp(clock(), UTC),
        placeholder_remarks=DEVICE_LIMIT_REMARKS if "x-hwid-limit" in hwid_flags else None,
        extra_headers=hwid_flags,
        # Read from this request's header alone: never stored, logged, or kept beside a device.
        stale_client=None if document == "browser" else stale_client(user_agent),
    )
    return apply_response_headers(response, decision.headers)


def effective_expiry(record: UserRecord) -> str | None:
    """The expiry delivery shows: the calculated one once a schedule exists, else the stored one."""
    return record.schedule.expire_at if record.schedule is not None else record.expire_at


def subscription_user(record: UserRecord, language: FleetLanguage) -> SubscriptionUser:
    """One account projection for live delivery and preview; no private metadata travels."""
    return SubscriptionUser(
        username=record.username,
        uuid=record.uuid,
        short_id=record.sub_token,
        status=record.status,
        # The calculated expiry: a renewal due before the next enforcement tick (or a first use)
        # must not send an old expire= beside the new refill date.
        expires_at=datetime.fromisoformat(expiry) if (expiry := effective_expiry(record)) else None,
        used_bytes=record.used_traffic_bytes,
        period_traffic=record.period_traffic,
        limit_bytes=record.traffic_limit_bytes,
        lifetime_used_bytes=record.lifetime_traffic_bytes,
        limit_strategy=record.traffic_limit_strategy,
        reset_anchor_day=record.traffic_reset_anchor_day,
        schedule=record.schedule,
        language=language_or_english(language),
    )
