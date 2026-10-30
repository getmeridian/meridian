"""Connection pages rendered from the control service's current subscription and settings."""

from __future__ import annotations

import json
from functools import cache
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from meridian.control.page_support import render_support_html
from meridian.control.store import Store
from meridian.control.subscriptions import SubscriptionResponse, unknown_subscription
from meridian.core.inputs import is_ip
from meridian.core.languages import resolve_language
from meridian.core.notices import RenderedNotice
from meridian.pwa import ASSET_SEGMENT, SECURITY_HEADERS, PwaAssets, generate_client_files, load_pwa_assets
from meridian.subscription_links import subscription_protocol_urls

if TYPE_CHECKING:
    from meridian.control.service import ControlService

_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "support.html": "text/html; charset=utf-8",
    "config.json": "application/json; charset=utf-8",
    "manifest.webmanifest": "application/manifest+json; charset=utf-8",
}


@cache
def packaged_assets() -> PwaAssets:
    """Installed assets are immutable for this service process."""
    return load_pwa_assets()


class ConnectionPages:
    def __init__(self, store: Store, service: ControlService) -> None:
        self._store = store
        self._service = service

    def render(self, token: str, filename: str = "index.html") -> SubscriptionResponse:
        # Settings, credentials, links, and display names belong to the same read.
        with self._store.lock:
            response = self._service.subscription(token, "info")
            if response.status != 200 or filename not in _TYPES:
                return unknown_subscription()
            info = json.loads(response.body)
            settings = self._service.settings()
            canonical = info["subscriptionUrl"] or f"/sub/{token}"
            page_path = urlsplit(canonical).path
            prefix = urlsplit(settings.public_url).path
            host = urlsplit(settings.public_url).hostname or ""
            name = self._display_name(info["user"]["username"])
            person = self._service.get_user(info["user"]["username"])
            if person is None:
                return unknown_subscription()
            language = resolve_language(self._service.languages.read().language, person.language)
        # Rendering and QR work do not hold the service's state lock.
        assets = packaged_assets()
        asset_path = f"{prefix}/sub/{ASSET_SEGMENT}/{assets.version}"
        if filename == "support.html":
            html = render_support_html(
                assets.files["support.html"],
                settings.owner_contact,
                language=language,
                asset_path=asset_path,
                page_path=page_path,
            )
            return SubscriptionResponse(200, _TYPES[filename], html.decode("utf-8"), dict(SECURITY_HEADERS))
        rendered = generate_client_files(
            subscription_protocol_urls("\n".join(info["links"])),
            server_ip=host if is_ip(host) else "",
            domain="" if is_ip(host) else host,
            client_name=name,
            server_name=settings.server_name,
            server_icon=settings.server_icon,
            color=settings.color,
            subscription_url=canonical,
            exit_countries=settings.exit_countries,
            owner_contact=settings.owner_contact,
            notice=RenderedNotice.model_validate(info["notice"]) if "notice" in info else None,
            language=language,
            asset_path=asset_path,
            page_path=page_path,
            worker_path=f"{prefix}/sub/{ASSET_SEGMENT}/sw.js",
            worker_scope=f"{prefix}/sub/",
        )
        return SubscriptionResponse(200, _TYPES[filename], rendered[filename], dict(SECURITY_HEADERS))

    def shared_support(self) -> bytes:
        """Compatibility support HTML has current fleet settings and no person token."""
        with self._store.lock:
            settings = self._service.settings()
            language = self._service.languages.read().language
        assets = packaged_assets()
        prefix = urlsplit(settings.public_url).path
        return render_support_html(
            assets.files["support.html"],
            settings.owner_contact,
            language=language,
            asset_path=f"{prefix}/sub/{ASSET_SEGMENT}/{assets.version}",
        )

    def legacy(self, user_uuid: str, filename: str = "") -> SubscriptionResponse:
        """Resolve old UUID links, including imported uppercase UUIDs; revoked ones are ordinary misses."""
        with self._store.lock:
            row = self._store.row("SELECT sub_token FROM users WHERE lower(uuid) = ?", (user_uuid.lower(),))
            if row is None:
                return unknown_subscription()
            prefix = urlsplit(self._service.settings().public_url).path
            target = f"{prefix}/sub/{row['sub_token']}"
            if filename and filename != "index.html":
                target += f"/{filename}"
        return SubscriptionResponse(302, "text/html; charset=utf-8", "", dict(SECURITY_HEADERS) | {"Location": target})

    def _display_name(self, username: str) -> str:
        from meridian.control.ui_views import applied_intent

        _, intent = applied_intent(self._service)
        access = intent.get("access")
        names = access.get("display_names") if isinstance(access, dict) else None
        shown = names.get(username) if isinstance(names, dict) else None
        return shown if isinstance(shown, str) and shown else username
