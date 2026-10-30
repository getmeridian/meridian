"""Read-only subscription selection, omissions, and redacted service headers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import quote

from meridian.control.devices import browser_request
from meridian.control.errors import ServiceError
from meridian.control.formats.capabilities import endpoint_omission, requires_primary, supported_endpoints
from meridian.control.formats.entries import SubscriptionUser, ordered, presentation_endpoints, primaries
from meridian.control.formats.selector import explain_format
from meridian.control.models import ClientEndpoint, PrivacySettings, ServiceSettings, UserRecord
from meridian.control.store import Store
from meridian.control.subscription_serving import subscription_user
from meridian.control.subscriptions import JSON_TYPE, TEXT_TYPE, YAML_TYPE, render_subscription, subscription_headers
from meridian.core.languages import LanguageState, resolve_language
from meridian.core.notices import RenderedNotice
from meridian.core.redaction import REDACTED, redact_preview_text
from meridian.core.subscription_preview import (
    FormatSelection,
    OmittedEntry,
    PreviewEntry,
    ResponseKind,
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
) -> SubscriptionPreview:
    decision = explain_format(request.user_agent, request.accept, request.suffix)
    with store.lock:
        record = read_user(request.username)
        if record is None:
            raise ServiceError("not_found", "Unknown person. Check names with meridian client list.", 404)
        rows = store.rows("SELECT endpoint_id, body FROM endpoints ORDER BY endpoint_id")
        if len(rows) > 10_000:
            raise ServiceError("invalid_request", "This fleet has too many endpoints to preview.", 400)
        endpoints = [ClientEndpoint.model_validate_json(row["body"]) for row in rows]
        identifiers = {id(endpoint): str(row["endpoint_id"]) for endpoint, row in zip(endpoints, rows, strict=True)}
        settings = read_settings()
        notice = read_notice(record)
        user = subscription_user(record, resolve_language(read_language().language, record.language))
        now = datetime.fromtimestamp(clock(), UTC)
        hwid_active = (
            read_privacy().device_limits != "off"
            and decision.format != "browser"
            and not browser_request(request.user_agent, request.accept)
        )
    known = [record.uuid, record.sub_token]
    for endpoint in endpoints:
        known.extend(
            (endpoint.reality_public_key, endpoint.reality_short_id, endpoint.salamander_password, endpoint.path)
        )
    known += [quote(value, safe="") for value in known if value]
    eligible = supported_endpoints(presentation_endpoints(endpoints), decision.format)
    connections = primaries(eligible) if requires_primary(decision.format) else eligible
    placeholder = user.status if user.status != "active" else "no_connections" if not connections else None
    included: list[PreviewEntry] = []
    omitted: list[OmittedEntry] = []
    shown = presentation_endpoints(endpoints, include_disabled=True)
    for original, endpoint in zip(ordered(endpoints), shown, strict=True):
        metadata = PreviewEntry(
            id=redact_preview_text(identifiers[id(original)], known),
            remark=redact_preview_text(endpoint.remark, known),
            protocol=endpoint.protocol,
            fingerprint_copy=endpoint.fingerprint_copy,
        )
        reason = endpoint_omission(endpoint, decision.format)
        if endpoint.enabled and user.status != "active":
            reason = "status_placeholder", None
        elif reason is None and placeholder == "no_connections":
            reason = "availability_placeholder", None
        if reason is None:
            included.append(metadata)
        else:
            omitted.append(OmittedEntry(**metadata.to_data(), reason=reason[0], restriction=reason[1]))
    response_kind: ResponseKind = (
        "subscription" if decision.format != "browser" else "account_info" if request.suffix else "page"
    )
    headers = preview_headers(user, decision, settings, notice, now)
    if hwid_active:
        headers["x-hwid-active"] = "true"
    safe_headers = {}
    for name, value in headers.items():
        name = name.lower()
        if name in ("support-url", "profile-web-page-url"):
            safe_headers[name] = REDACTED
        elif name in ("profile-title", "announce"):
            safe_headers[name] = "base64:" + REDACTED
        else:
            safe_headers[name] = redact_preview_text(value, known)
    return SubscriptionPreview(
        **decision.to_data(),
        username=redact_preview_text(record.username, known),
        status=user.status,
        response_kind=response_kind,
        protocols=[protocol for protocol in PROTOCOL_PRIORITY if any(entry.protocol == protocol for entry in included)],
        included=included,
        omitted=omitted,
        placeholder=placeholder,
        response_headers=safe_headers,
    )


def preview_headers(
    user: SubscriptionUser,
    decision: FormatSelection,
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
