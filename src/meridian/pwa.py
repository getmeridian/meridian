"""Connection-page rendering and content-versioned assets, shared by ctld and offline preview."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from meridian.render import (
    render_apps_catalog,
    render_config_json,
    render_manifest,
    render_pwa_shell,
)

if TYPE_CHECKING:
    from meridian.models import ProtocolURL

# Static PWA assets shipped with the package (relative to templates/pwa/).
_STATIC_FILES = (
    "app.js",
    "account.js",
    "selfcheck.js",
    "styles.css",
    "sw.js",
    "icon.svg",
    "support.html",
    "support.js",
)
# Placeholder in sw.js (cache name) and selfcheck.js (reported page version).
_VERSION_PLACEHOLDER = b"'pwa-v1'"

# The self-check streams this file and watches for a throttling freeze, so it
# must clear diagnostics.blocking.STALL_CLEARANCE_BYTES with room to spare and
# stay incompressible (nginx does not gzip octet-stream anyway).
PROBE_FILE = "probe.bin"
PROBE_BYTES = 96 * 1024
ASSET_SEGMENT = "~pwa"  # The subscription token alphabet has no tilde.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "manifest-src 'self'; worker-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
}


@dataclass(frozen=True)
class PwaAssets:
    version: str
    files: dict[str, bytes]


def probe_payload() -> bytes:
    """Deterministic pseudo-random bytes, so re-uploads are byte-identical."""
    return hashlib.shake_256(b"meridian-selfcheck").digest(PROBE_BYTES)


def generate_client_files(
    protocol_urls: list[ProtocolURL],
    server_ip: str,
    domain: str = "",
    *,
    client_name: str = "",
    server_name: str = "",
    server_icon: str = "",
    color: str = "",
    subscription_url: str = "",
    exit_countries: list[str] | None = None,
    asset_path: str = "../pwa",
    page_path: str = "",
    worker_path: str = "../pwa/sw.js",
    worker_scope: str = "../",
) -> dict[str, str]:
    """Generate all per-client PWA files as a {filename: content} dict.

    The control service's subscription URL is canonical; Meridian does not synthesize a
    second subscription from local topology state.
    """
    return {
        "index.html": render_pwa_shell(
            client_name=client_name,
            server_name=server_name,
            asset_path=asset_path,
            config_path=f"{page_path}/config.json" if page_path else "config.json",
            manifest_path=f"{page_path}/manifest.webmanifest" if page_path else "manifest.webmanifest",
            worker_path=worker_path,
            worker_scope=worker_scope,
        ),
        "config.json": render_config_json(
            protocol_urls,
            server_ip=server_ip,
            domain=domain,
            client_name=client_name,
            server_name=server_name,
            server_icon=server_icon,
            color=color,
            subscription_url=subscription_url,
            exit_countries=exit_countries,
        ),
        "manifest.webmanifest": render_manifest(
            client_name=client_name, server_name=server_name, asset_path=asset_path, start_url=page_path or "."
        ),
    }


def load_pwa_static_assets() -> dict[str, bytes]:
    """Load shared PWA assets: package files plus the generated catalog and probe.

    Returns a dict mapping filename to file content bytes.
    """
    from importlib.resources import files

    pwa_dir = files("meridian") / "templates" / "pwa"
    assets: dict[str, bytes] = {}
    for name in _STATIC_FILES:
        resource = pwa_dir / name
        assets[name] = resource.read_bytes()
    assets["apps.json"] = render_apps_catalog().encode("utf-8")
    assets[PROBE_FILE] = probe_payload()
    return assets


def load_pwa_assets() -> PwaAssets:
    """Version every shipped asset by its bytes; the stable worker URL revalidates."""
    assets = load_pwa_static_assets()
    digest = hashlib.sha256()
    for name, body in sorted(assets.items()):
        digest.update(name.encode("ascii") + b"\0" + body)
    version = f"pwa-{digest.hexdigest()[:16]}"
    for name in ("sw.js", "selfcheck.js"):
        assets[name] = assets[name].replace(_VERSION_PLACEHOLDER, f"'{version}'".encode("ascii"))
    return PwaAssets(version, assets)
