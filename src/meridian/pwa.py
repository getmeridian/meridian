"""PWA file generation and deployment helpers.

Centralizes the creation and upload of per-client PWA files
(index.html, config.json, manifest.webmanifest) and
shared static assets (app.js, styles.css, sw.js, icon.svg).

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
    render_config_json,
    render_manifest,
    render_pwa_shell,
)

if TYPE_CHECKING:
    from meridian.cluster import ClusterConfig
    from meridian.models import ProtocolURL, RelayURLSet
    from meridian.ssh import ServerConnection

# Static PWA assets shipped with the package (relative to templates/pwa/).
_STATIC_FILES = ("app.js", "styles.css", "sw.js", "icon.svg")


def _page_marker(user_uuid: str) -> str:
    return hashlib.sha256(user_uuid.encode("utf-8")).hexdigest()


def connection_page_deployed(cluster: ClusterConfig, user_uuid: str) -> bool:
    """Return persisted evidence that this client's page upload completed."""
    return _page_marker(user_uuid) in cluster.connection_page_markers


def mark_connection_page_deployed(cluster: ClusterConfig, user_uuid: str) -> None:
    """Record non-secret page deployment evidence in cluster state."""
    cluster.connection_page_markers = sorted({*cluster.connection_page_markers, _page_marker(user_uuid)})


def generate_client_files(
    protocol_urls: list[ProtocolURL],
    server_ip: str,
    domain: str = "",
    *,
    client_name: str = "",
    relay_entries: list[RelayURLSet] | None = None,
    server_name: str = "",
    server_icon: str = "",
    color: str = "",
    subscription_url: str = "",
) -> dict[str, str]:
    """Generate all per-client PWA files as a {filename: content} dict.

    Remnawave's subscription URL is canonical; Meridian does not synthesize a
    second subscription from local topology state.
    """
    return {
        "index.html": render_pwa_shell(client_name=client_name, server_name=server_name),
        "config.json": render_config_json(
            protocol_urls,
            server_ip=server_ip,
            domain=domain,
            client_name=client_name,
            relay_entries=relay_entries,
            server_name=server_name,
            server_icon=server_icon,
            color=color,
            subscription_url=subscription_url,
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
    """Load shared PWA static assets from package data.

    Returns a dict mapping filename to file content bytes.
    """
    from importlib.resources import files

    pwa_dir = files("meridian") / "templates" / "pwa"
    assets: dict[str, bytes] = {}
    for name in _STATIC_FILES:
        resource = pwa_dir / name
        assets[name] = resource.read_bytes()
    return assets


def upload_pwa_assets(conn: ServerConnection) -> str:
    """Upload shared PWA static assets to ``/var/www/private/pwa/``.

    Deploys app.js, styles.css, sw.js, and icon.svg. These are
    identical for all clients and shared across connection pages.

    The service worker's ``CACHE_VERSION`` is replaced with a
    content-derived hash so browsers automatically invalidate
    stale caches when assets change.

    Returns empty string on success, error detail on failure.
    """
    assets = load_pwa_static_assets()

    # Compute content hash from cacheable assets for SW cache busting
    h = hashlib.sha256()
    for name in ("app.js", "styles.css"):
        if name in assets:
            h.update(assets[name])
    cache_version = f"pwa-{h.hexdigest()[:8]}"

    # Inject dynamic cache version into sw.js
    if "sw.js" in assets:
        assets["sw.js"] = assets["sw.js"].replace(b"'pwa-v1'", f"'{cache_version}'".encode())

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
