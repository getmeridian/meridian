"""The subscription answer for one request: format, body, and the headers apps read.

Bodies and headers follow the golden corpus captured from Remnawave
(`tests/systemlab/golden/`) with Meridian's fixes (see `formats.selector` and the
corpus test). Compression is nginx's: the service always answers uncompressed, and
nginx gzips JSON and Base64 but leaves YAML as it is (`gzip_types`), as with Remnawave.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote

from meridian.control.formats.browser import NEVER_EXPIRES, not_found, subscription_info
from meridian.control.formats.contact_remarks import contact_remarks
from meridian.control.formats.entries import SubscriptionUser
from meridian.control.formats.links import base64_document, links
from meridian.control.formats.mihomo import mihomo_document
from meridian.control.formats.selector import DocumentFormat
from meridian.control.formats.singbox import singbox_document
from meridian.control.formats.xray_json import xray_json_document
from meridian.control.models import ClientEndpoint
from meridian.control.periods import next_period_start
from meridian.core.defaults import DEFAULT_CLIENT_ROUTING, SUBSCRIPTION_UPDATE_INTERVAL_HOURS, SUPPORT_PAGE
from meridian.core.owner_contact import OwnerContact
from meridian.core.topology import ClientRoutingPolicy

JSON_TYPE = "application/json; charset=utf-8"
TEXT_TYPE = "text/plain; charset=utf-8"
YAML_TYPE = "text/yaml; charset=utf-8"
# Configs and /info JSON. Pages carry their own browser protections; nginx adds
# Referrer-Policy. The panel's CSP naming third parties never belonged here.
COMMON_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-robots-tag": "noindex, nofollow, noarchive, nosnippet, noimageindex",
    "access-control-allow-origin": "*",
}
_PLAIN_FILENAME = re.compile(r"^[A-Za-z0-9._-]+$")
DEVICE_LIMIT_REMARKS = (
    "Device limit reached: remove a device or ask for a higher limit",
    "Достигнут лимит устройств: удалите устройство или попросите увеличить лимит",
    "تعداد دستگاه‌ها به سقف رسیده است: یک دستگاه را حذف کنید یا درخواست افزایش سقف بدهید",
    "已达到设备上限：请移除一台设备或申请提高上限",
)


@dataclass(frozen=True)
class SubscriptionResponse:
    status: int
    content_type: str
    body: str
    headers: dict[str, str] = field(default_factory=dict)


def render_subscription(
    user: SubscriptionUser,
    endpoints: list[ClientEndpoint],
    document: DocumentFormat,
    *,
    profile_title: str,
    public_url: str = "",
    update_interval_hours: int = SUBSCRIPTION_UPDATE_INTERVAL_HOURS,
    client_routing: ClientRoutingPolicy = DEFAULT_CLIENT_ROUTING,
    now: datetime | None = None,
    placeholder_remarks: tuple[str, ...] | None = None,
    extra_headers: dict[str, str] | None = None,
    owner_contact: OwnerContact | None = None,
) -> SubscriptionResponse:
    """Render *document* for *user*; a suspended user's entries are status placeholders."""
    placeholders = user.placeholders if placeholder_remarks is None else placeholder_remarks
    if owner_contact is not None and user.status != "active" and placeholder_remarks is None:
        placeholders = contact_remarks(placeholders, owner_contact.text)
    moment = now or datetime.now(UTC)
    if document == "browser":
        info = subscription_info(
            user,
            links(endpoints, user.uuid, placeholders),
            subscription_url(public_url, user.short_id),
            moment,
        )
        return SubscriptionResponse(200, JSON_TYPE, _json(info), dict(COMMON_HEADERS) | _refill_headers(user, moment))
    headers = subscription_headers(
        user,
        profile_title=profile_title,
        public_url=public_url,
        update_interval_hours=update_interval_hours,
        support_link=owner_contact.link if owner_contact is not None else "",
        now=moment,
    )
    headers.update(extra_headers or {})
    if document == "xray-json":
        body = _json(xray_json_document(endpoints, user.uuid, placeholders, client_routing=client_routing))
        return SubscriptionResponse(200, JSON_TYPE, body, headers)
    if document == "singbox":
        return SubscriptionResponse(
            200,
            JSON_TYPE,
            _json(singbox_document(endpoints, user.uuid, placeholders, client_routing=client_routing)),
            headers,
        )
    if document in ("mihomo", "stash"):
        body = mihomo_document(
            endpoints, user.uuid, placeholders, stash=document == "stash", client_routing=client_routing
        )
        return SubscriptionResponse(200, YAML_TYPE, body, headers)
    return SubscriptionResponse(200, TEXT_TYPE, base64_document(endpoints, user.uuid, placeholders), headers)


def unknown_subscription() -> SubscriptionResponse:
    return SubscriptionResponse(404, JSON_TYPE, _json(not_found()), dict(COMMON_HEADERS))


def subscription_headers(
    user: SubscriptionUser,
    *,
    profile_title: str,
    public_url: str = "",
    update_interval_hours: int = SUBSCRIPTION_UPDATE_INTERVAL_HOURS,
    now: datetime | None = None,
    support_link: str = "",
) -> dict[str, str]:
    """The headers apps read: name, traffic and expiry, refresh interval, and page links."""
    headers = dict(COMMON_HEADERS)
    headers.update(_refill_headers(user, now or datetime.now(UTC)))
    headers["content-disposition"] = _disposition(user.username)
    # Upload and download are one total until per-direction counters exist; `expire` 0 is "never".
    expire = 0
    if user.expires_at is not None and user.expires_at < NEVER_EXPIRES:
        expire = int(user.expires_at.timestamp())
    headers["subscription-userinfo"] = (
        f"upload=0; download={user.used_bytes}; total={user.limit_bytes}; expire={expire}"
    )
    if support_link:
        headers["support-url"] = support_link
    elif public_url:
        headers["support-url"] = f"{public_url}/{SUPPORT_PAGE}#{quote(user.short_id, safe='')}"
    headers["profile-title"] = "base64:" + base64.b64encode(profile_title.encode("utf-8")).decode("ascii")
    if public_url:
        headers["profile-web-page-url"] = subscription_url(public_url, user.short_id)
    headers["profile-update-interval"] = str(update_interval_hours)
    return headers


def subscription_url(public_url: str, short_id: str) -> str:
    """The URL apps subscribe to (and a browser opens); empty without a public URL."""
    return f"{public_url}/sub/{quote(short_id, safe='')}" if public_url else ""


def _refill_headers(user: SubscriptionUser, now: datetime) -> dict[str, str]:
    next_start = next_period_start(user.limit_strategy, now)
    return {"subscription-refill-date": str(int(next_start.timestamp()))} if next_start is not None else {}


def _disposition(username: str) -> str:
    if _PLAIN_FILENAME.fullmatch(username):
        return f"attachment; filename={username}"
    return f"attachment; filename*=UTF-8''{quote(username, safe='')}"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
