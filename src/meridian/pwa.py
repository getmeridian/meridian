"""PWA file generation and deployment helpers.

Centralizes the creation and upload of per-client PWA files
(index.html, config.json, manifest.webmanifest) and
shared assets (app.js, selfcheck.js, styles.css, sw.js, icon.svg, plus the
generated apps.json catalog and probe.bin self-check payload).

Called from:
- ``DeployPWAAssets`` provisioner step (shared static assets)
- ``meridian.handoff`` (per-client pages built from each subscription)
- ``commands/dev.py`` (local preview)
"""

from __future__ import annotations

import hashlib
import shlex
from typing import TYPE_CHECKING

from meridian.render import (
    render_apps_catalog,
    render_config_json,
    render_manifest,
    render_pwa_shell,
)

if TYPE_CHECKING:
    from meridian.cluster import ClusterConfig
    from meridian.models import ProtocolURL
    from meridian.ssh import ServerConnection

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
# Assets whose bytes feed the service worker's cache version.
_VERSIONED_ASSETS = ("app.js", "account.js", "selfcheck.js", "styles.css", "apps.json")
# Placeholder in sw.js (cache name) and selfcheck.js (reported page version).
_VERSION_PLACEHOLDER = b"'pwa-v1'"

# The self-check streams this file and watches for a throttling freeze, so it
# must clear diagnostics.blocking.STALL_CLEARANCE_BYTES with room to spare and
# stay incompressible (nginx does not gzip octet-stream anyway).
PROBE_FILE = "probe.bin"
PROBE_BYTES = 96 * 1024


def probe_payload() -> bytes:
    """Deterministic pseudo-random bytes, so re-uploads are byte-identical."""
    return hashlib.shake_256(b"meridian-selfcheck").digest(PROBE_BYTES)


def _page_marker(user_uuid: str) -> str:
    return hashlib.sha256(user_uuid.encode("utf-8")).hexdigest()


def connection_page_deployed(cluster: ClusterConfig, user_uuid: str) -> bool:
    """Return persisted evidence that this client's page upload completed."""
    return _page_marker(user_uuid) in cluster.connection_page_markers


def mark_connection_page_deployed(cluster: ClusterConfig, user_uuid: str) -> None:
    """Record non-secret page deployment evidence in cluster state."""
    cluster.connection_page_markers = sorted({*cluster.connection_page_markers, _page_marker(user_uuid)})


def forget_connection_page(cluster: ClusterConfig, user_uuid: str) -> None:
    """Drop page deployment evidence after the page was removed."""
    marker = _page_marker(user_uuid)
    cluster.connection_page_markers = [item for item in cluster.connection_page_markers if item != marker]


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
) -> dict[str, str]:
    """Generate all per-client PWA files as a {filename: content} dict.

    The control service's subscription URL is canonical; Meridian does not synthesize a
    second subscription from local topology state.
    """
    return {
        "index.html": render_pwa_shell(client_name=client_name, server_name=server_name),
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
        "manifest.webmanifest": render_manifest(client_name=client_name, server_name=server_name),
    }


def upload_client_files(
    conn: ServerConnection,
    reality_uuid: str,
    files: dict[str, str],
) -> str:
    """Upload per-client PWA files to ``/var/www/private/{uuid}/``.

    Returns empty string on success, error detail on failure.
    """
    q_uuid = shlex.quote(reality_uuid)
    result = conn.run(
        f"mkdir -p /var/www/private/{q_uuid} && chown www-data:www-data /var/www/private/{q_uuid}",
        timeout=30,
        sensitive=True,
    )
    if result.returncode != 0:
        return f"Failed to create client directory: {result.stderr.strip()[:200]}"
    for filename, content in files.items():
        result = conn.put_text(
            f"/var/www/private/{reality_uuid}/{filename}",
            content,
            mode="644",
            owner="www-data:www-data",
            timeout=30,
            operation_name=f"upload client file {filename}",
            sensitive=True,
        )
        if result.returncode != 0:
            return f"Failed to upload {filename}: {result.stderr.strip()[:200]}"
    return ""


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


def upload_pwa_assets(conn: ServerConnection) -> str:
    """Upload shared PWA static assets to ``/var/www/private/pwa/``.

    Deploys every asset from ``load_pwa_static_assets()``. These are
    identical for all clients and shared across connection pages.

    The service worker's ``CACHE_VERSION`` and the self-check's reported
    page version are replaced with a content-derived hash, so browsers
    invalidate stale caches and reports name the exact deployed page.

    Returns empty string on success, error detail on failure.
    """
    assets = load_pwa_static_assets()

    # Compute content hash from cacheable assets for SW cache busting
    h = hashlib.sha256()
    for name in _VERSIONED_ASSETS:
        if name in assets:
            h.update(assets[name])
    cache_version = f"pwa-{h.hexdigest()[:8]}"

    for name in ("sw.js", "selfcheck.js"):
        if name in assets:
            assets[name] = assets[name].replace(_VERSION_PLACEHOLDER, f"'{cache_version}'".encode())

    result = conn.run("mkdir -p /var/www/private/pwa && chown www-data:www-data /var/www/private/pwa", timeout=30)
    if result.returncode != 0:
        return f"Failed to create /var/www/private/pwa/: {result.stderr.strip()[:200]}"

    for filename, content in assets.items():
        result = conn.put_bytes(
            f"/var/www/private/pwa/{filename}",
            content,
            mode="644",
            owner="www-data:www-data",
            timeout=30,
            operation_name=f"upload pwa asset {filename}",
            sensitive=True,
        )
        if result.returncode != 0:
            return f"Failed to upload pwa/{filename}: {result.stderr.strip()[:200]}"
    return ""
