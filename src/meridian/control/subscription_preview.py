"""Read-only subscription selection, omissions, and redacted service headers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import quote

from meridian.control.access_memberships import eligible_endpoints
from meridian.control.delivery_preview import preview_delivery_policy
from meridian.control.devices import browser_request
from meridian.control.errors import ServiceError
from meridian.control.formats.capabilities import endpoint_omission, requires_primary, supported_endpoints
from meridian.control.formats.entries import (
    SubscriptionUser,
    ordered,
    presentation_endpoints,
    presentation_pairs,
    primaries,
)
from meridian.control.formats.response_rules import evaluate_response_rules, merge_response_headers
from meridian.control.formats.stale_clients import stale_client
from meridian.control.models import ClientEndpoint, PrivacySettings, ServiceSettings, UserRecord
from meridian.control.response_rule_testing import safe_response_decision, safe_response_headers
from meridian.control.store import Store
from meridian.control.subscription_serving import subscription_user
from meridian.control.subscriptions import (
    JSON_TYPE,
    TEXT_TYPE,
    YAML_TYPE,
    render_subscription,
    subscription_headers,
    unknown_subscription,
)
from meridian.core.access_profiles import may_use_inbound
from meridian.core.delivery_explain import DeliveryExplainRequest, explain_delivery_policy
from meridian.core.delivery_geodata import GeoData
from meridian.core.languages import LanguageState, resolve_language
from meridian.core.notices import RenderedNotice
from meridian.core.redaction import redact_preview_text
from meridian.core.response_rule_test import ResponseRuleDecision
from meridian.core.subscription_preview import (
    OmittedEntry,
    PreviewEntry,
    SubscriptionPreview,
    SubscriptionPreviewRequest,
)
from meridian.core.topology import PROTOCOL_PRIORITY
from meridian.pwa import SECURITY_HEADERS


def preview_subscription(
    store: Store,
    request: SubscriptionPreviewRequest,
    *,
    read_user: Callable[[str], UserRecord | None],
    read_settings: Callable[[], ServiceSettings],
    read_notice: Callable[[UserRecord], RenderedNotice | None],
    read_language: Callable[[], LanguageState],
    read_privacy: Callable[[], PrivacySettings],
    clock: Callable[[], float],
    include_delivery: bool = True,
    read_geodata: Callable[[], GeoData | None] = lambda: None,
) -> SubscriptionPreview:
    with store.lock:
        record = read_user(request.username)
        if record is None:
            raise ServiceError("not_found", "Unknown person. Check names with meridian client list.", 404)
        settings = read_settings()
        decision = evaluate_response_rules(
            settings,
            user_agent=request.user_agent,
            accept=request.accept,
            suffix=request.suffix,
            profiles=record.profiles,
            person_override=record.delivery_policy,
        )
        document = decision.format
        if document is None:
            refusal = unknown_subscription()
            return SubscriptionPreview(
                format=None,
                selection=decision.selection,
                response_rule=safe_response_decision(decision),
                username=redact_preview_text(record.username, [record.uuid, record.sub_token]),
                status=record.status,
                response_kind="blocked",
                protocols=[],
                included=[],
                omitted=[],
                placeholder=None,
                response_headers=refusal.headers
                | {
                    "content-type": refusal.content_type,
                    "cache-control": "no-store",
                },
            )
        rows = store.rows("SELECT endpoint_id, body FROM endpoints ORDER BY endpoint_id")
        if len(rows) > 10_000:
            raise ServiceError("invalid_request", "This fleet has too many endpoints to preview.", 400)
        endpoints = [ClientEndpoint.model_validate_json(row["body"]) for row in rows]
        identifiers = {id(endpoint): str(row["endpoint_id"]) for endpoint, row in zip(endpoints, rows, strict=True)}
        resolved_policy = decision.policy
        assert resolved_policy is not None
        notice = read_notice(record)
        user = subscription_user(record, resolve_language(read_language().language, record.language))
        now = datetime.fromtimestamp(clock(), UTC)
        hwid_active = (
            read_privacy().device_limits != "off"
            and document != "browser"
            and not browser_request(request.user_agent, request.accept)
        )
    known = [record.uuid, record.sub_token]
    for endpoint in endpoints:
        known.extend(
            (endpoint.reality_public_key, endpoint.reality_short_id, endpoint.salamander_password, endpoint.path)
        )
    known += [quote(value, safe="") for value in known if value]
    allowed = eligible_endpoints(record, endpoints, settings.access_profiles)
    allowed_names = {id(original): named for original, named in presentation_pairs(allowed)}
    eligible = supported_endpoints(presentation_endpoints(allowed), document)
    connections = primaries(eligible) if requires_primary(document) else eligible
    placeholder = user.status if user.status != "active" else "no_connections" if not connections else None
    included: list[PreviewEntry] = []
    omitted: list[OmittedEntry] = []
    shown = presentation_endpoints(endpoints, include_disabled=True)
    for original, endpoint in zip(ordered(endpoints), shown, strict=True):
        endpoint = allowed_names.get(id(original), endpoint)
        metadata = PreviewEntry(
            id=redact_preview_text(identifiers[id(original)], known),
            remark=redact_preview_text(endpoint.remark, known),
            protocol=endpoint.protocol,
            fingerprint_copy=endpoint.fingerprint_copy,
        )
        reason = endpoint_omission(endpoint, document)
        if not may_use_inbound(record.profiles, settings.access_profiles, endpoint.inbound_ref):
            reason = "access_profile", None
        elif endpoint.enabled and user.status != "active":
            reason = "status_placeholder", None
        elif reason is None and placeholder == "no_connections":
            reason = "availability_placeholder", None
        if reason is None:
            included.append(metadata)
        else:
            omitted.append(OmittedEntry(**metadata.to_data(), reason=reason[0], restriction=reason[1]))
    # The same rule as live delivery: only an app document with real entries carries the nudge.
    stale = stale_client(request.user_agent) if document != "browser" and placeholder is None else None
    headers = merge_response_headers(preview_headers(user, decision, settings, notice, now), decision.headers)
    if hwid_active:
        headers["x-hwid-active"] = "true"
    return SubscriptionPreview(
        delivery=preview_delivery_policy(resolved_policy, presentation_endpoints(allowed))
        if include_delivery
        else None,
        explanation=explain_delivery_policy(
            resolved_policy,
            DeliveryExplainRequest(destination=request.destination, person=request.username, format=document),
            available_exits={endpoint.exit_ref for endpoint in connections if endpoint.exit_ref},
            geodata=read_geodata(),
            available=bool(connections),
            active=user.status == "active",
        )
        if include_delivery and request.destination is not None
        else None,
        format=document,
        selection=decision.selection,
        response_rule=safe_response_decision(decision, include_policy=include_delivery)
        if decision.selection.rule == "custom"
        else None,
        username=redact_preview_text(record.username, known),
        status=user.status,
        response_kind=decision.response_kind,
        protocols=[protocol for protocol in PROTOCOL_PRIORITY if any(entry.protocol == protocol for entry in included)],
        included=included,
        omitted=omitted,
        placeholder=placeholder,
        stale_client=stale,
        response_headers=safe_response_headers(headers, known),
    )


def preview_headers(
    user: SubscriptionUser,
    decision: ResponseRuleDecision,
    settings: ServiceSettings,
    notice: RenderedNotice | None,
    now: datetime,
) -> dict[str, str]:
    if decision.format == "browser" and not decision.selection.suffix:
        return dict(SECURITY_HEADERS) | {"content-type": "text/html; charset=utf-8", "cache-control": "no-store"}
    if decision.format == "browser":
        response = render_subscription(user, [], "browser", profile_title=settings.profile_title, now=now)
        return response.headers | {"content-type": response.content_type, "cache-control": "no-store"}
    headers = subscription_headers(
        user,
        profile_title=settings.profile_title,
        public_url=settings.public_url,
        update_interval_hours=settings.update_interval_hours,
        support_link=settings.owner_contact.link,
        notice=notice,
        now=now,
    )
    content_type = (
        YAML_TYPE if decision.format in ("mihomo", "stash") else TEXT_TYPE if decision.format == "base64" else JSON_TYPE
    )
    return headers | {"content-type": content_type, "cache-control": "no-store"}
