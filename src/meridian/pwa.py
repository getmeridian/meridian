"""PWA file generation and deployment helpers.

Centralizes the creation and upload of per-client PWA files
(index.html, config.json, manifest.webmanifest) and
shared static assets (app.js, styles.css, sw.js, icon.svg).

Called from:
- ``DeployPWAAssets`` provisioner step (shared static assets)
- ``commands/client.py`` (_deploy_client_page — per-client pages)
- ``commands/relay.py`` (relay page regeneration)
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
    from meridian.cluster import ClusterConfig, NodeEntry
    from meridian.models import ProtocolURL, RelayURLSet
    from meridian.ssh import ServerConnection

# Static PWA assets shipped with the package (relative to templates/pwa/).
_STATIC_FILES = ("app.js", "styles.css", "sw.js", "icon.svg")


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


def deploy_client_page(
    conn: ServerConnection,
    cluster: ClusterConfig,
    node: NodeEntry,
    user_uuid: str,
    client_name: str,
    sub_url: str = "",
) -> str:
    """Generate and upload a PWA connection page for a client.

    Builds VLESS protocol URLs from cluster.yml node data + client UUID,
    generates QR codes and PWA files, uploads to the server.

    Returns the page URL on success, empty string on failure.
    """
    import logging

    from meridian.models import ProtocolURL
    from meridian.protocols import PROTOCOLS
    from meridian.urls import generate_qr_base64

    logger = logging.getLogger("meridian.pwa")

    host = node.domain or node.ip
    info_page_path = cluster.panel.sub_path or ""
    if not info_page_path:
        return ""
    if not sub_url:
        logger.warning("Cannot deploy connection page without the canonical Remnawave subscription URL")
        return ""

    page_url = f"https://{host}/{info_page_path}/{user_uuid}/"

    # Build protocol URLs using the protocol registry's build_url() methods
    protocol_urls: list[ProtocolURL] = []

    if node.reality_public_key:
        reality = PROTOCOLS.get("reality")
        if reality:
            url = reality.build_url(
                user_uuid,
                client_name,
                ip=node.ip,
                sni=node.sni,
                public_key=node.reality_public_key,
                short_id=node.reality_short_id or "",
                server_name=cluster.branding.server_name,
            )
            qr = generate_qr_base64(url)
            protocol_urls.append(ProtocolURL(key="reality", label=reality.display_label, url=url, qr_b64=qr))

    if node.xhttp_path:
        xhttp = PROTOCOLS.get("xhttp")
        if xhttp:
            url = xhttp.build_url(
                user_uuid,
                client_name,
                ip=node.ip,
                xhttp_path=node.xhttp_path,
                domain=node.domain or "",
                server_name=cluster.branding.server_name,
            )
            qr = generate_qr_base64(url)
            protocol_urls.append(ProtocolURL(key="xhttp", label=xhttp.display_label, url=url, qr_b64=qr))

    if node.domain and node.ws_path:
        wss = PROTOCOLS.get("wss")
        if wss:
            url = wss.build_url(
                user_uuid,
                client_name,
                domain=node.domain,
                ws_path=node.ws_path,
                server_name=cluster.branding.server_name,
            )
            qr = generate_qr_base64(url)
            protocol_urls.append(ProtocolURL(key="wss", label=wss.display_label, url=url, qr_b64=qr))

    if not protocol_urls:
        return ""

    files = generate_client_files(
        protocol_urls,
        server_ip=node.ip,
        domain=node.domain or "",
        client_name=client_name,
        server_name=cluster.branding.server_name,
        server_icon=cluster.branding.icon,
        color=cluster.branding.color,
        subscription_url=sub_url,
    )

    error = upload_client_files(conn, user_uuid, files)
    if error:
        logger.warning("Could not deploy connection page: %s", error)
        return ""

    return page_url
