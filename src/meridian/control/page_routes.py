"""Public PWA assets and compatibility routes inside nginx's subscription proxy."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

from meridian.control.connection_pages import packaged_assets
from meridian.control.subscriptions import unknown_subscription
from meridian.control.ui_routes import UiRequest
from meridian.pwa import ASSET_SEGMENT, SECURITY_HEADERS

_TYPES = {
    "js": "text/javascript; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "svg": "image/svg+xml",
    "html": "text/html; charset=utf-8",
    "json": "application/json; charset=utf-8",
}


def asset(request: UiRequest, match: re.Match[str]) -> None:
    _asset(request, match[1] or "", match[2])


def _asset(request: UiRequest, version: str, name: str) -> None:
    assets = packaged_assets()
    data = assets.files.get(name)
    if data is None or (version and version != assets.version):
        _unknown(request)
        return
    if name == "support.html" and not version:
        prefix = urlsplit(request.ui.service.settings().public_url).path
        request.respond(
            302,
            "text/html",
            b"",
            dict(SECURITY_HEADERS)
            | {
                "Location": f"{prefix}/sub/{ASSET_SEGMENT}/{assets.version}/support.html",
            },
        )
        return
    if name == "support.html":
        data = request.ui.service.pages.shared_support()
    headers = dict(SECURITY_HEADERS) | {
        "Cache-Control": "no-store"
        if name == "support.html"
        else "public, max-age=31536000, immutable"
        if version
        else "no-cache",
        "ETag": '"' + hashlib.sha256(data).hexdigest() + '"',
    }
    if name == "sw.js":
        # nginx's public prefix is absent on loopback; allow the page's narrower scope.
        headers["Service-Worker-Allowed"] = "/"
    if request.headers.get("If-None-Match") == headers["ETag"]:
        request.respond(304, _TYPES.get(name.rsplit(".", 1)[-1], "application/octet-stream"), b"", headers)
        return
    request.respond(200, _TYPES.get(name.rsplit(".", 1)[-1], "application/octet-stream"), data, headers)


def legacy_asset(request: UiRequest, match: re.Match[str]) -> None:
    # The old worker's stable URL must keep answering JavaScript after its files retire.
    _asset(request, "", match[1])


def page(request: UiRequest, match: re.Match[str]) -> None:
    response = request.ui.service.pages.render(match[1], match[2])
    request.respond(response.status, response.content_type, response.body.encode("utf-8"), response.headers)


def legacy(request: UiRequest, match: re.Match[str]) -> None:
    response = request.ui.service.pages.legacy(match[1], match[2] or "")
    request.respond(response.status, response.content_type, response.body.encode("utf-8"), response.headers)


def _unknown(request: UiRequest) -> None:
    response = unknown_subscription()
    request.respond(response.status, response.content_type, response.body.encode("utf-8"), response.headers)
