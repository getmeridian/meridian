"""End-user handoff: connection pages built from each user's canonical subscription.

The page lists exactly the links Remnawave serves to that user (direct exits,
relays, Hysteria2), so it can never drift from what client apps receive.
"""

from __future__ import annotations

import base64
import binascii
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

from meridian.cluster import ClusterConfig
from meridian.core.clients import PanelUserLike
from meridian.models import ProtocolURL
from meridian.remnawave import MeridianPanel, RemnawaveError, User
from meridian.ssh import ServerConnection

logger = logging.getLogger("meridian.handoff")

# Page labels for subscription links that carry no name of their own.
_PROTOCOL_LABELS = {"reality": "Primary", "xhttp": "XHTTP", "wss": "CDN Backup", "hysteria2": "UDP fallback"}


@dataclass(frozen=True)
class HandoffLinks:
    """What to give one end user: a page to open and a subscription to import."""

    username: str
    subscription_url: str = ""
    page_url: str = ""
    error: str = ""


def protocol_key(url: str) -> str:
    """Classify one subscription link into a Meridian protocol key."""
    parsed = urlsplit(url)
    if parsed.scheme in {"hysteria2", "hy2"}:
        return "hysteria2"
    params = parse_qs(parsed.query)
    if params.get("security") == ["reality"]:
        return "reality"
    if params.get("type") == ["xhttp"]:
        return "xhttp"
    if params.get("type") == ["ws"]:
        return "wss"
    return ""


def subscription_protocol_urls(content: str) -> list[ProtocolURL]:
    """Parse a base64 (or plain) subscription body into labelled links with QR codes."""
    from meridian.urls import generate_qr_base64

    text = content.strip()
    try:
        decoded = base64.b64decode(text + "=" * (-len(text) % 4), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        decoded = text
    links: list[ProtocolURL] = []
    for line in decoded.splitlines():
        url = line.strip()
        key = protocol_key(url) if "://" in url else ""
        if not key:
            continue
        label = unquote(urlsplit(url).fragment) or _PROTOCOL_LABELS[key]
        links.append(ProtocolURL(key=key, label=label, url=url, qr_b64=generate_qr_base64(url)))
    return links


def page_url(cluster: ClusterConfig, user: PanelUserLike) -> str:
    """Public connection-page URL for a user, or empty when it cannot be built."""
    if not cluster.panel.url or not cluster.panel.sub_path or not user.vless_uuid:
        return ""
    parsed = urlsplit(cluster.panel.url)
    return f"{parsed.scheme}://{parsed.netloc}/{cluster.panel.sub_path}/{user.vless_uuid}/"


def exit_countries(cluster: ClusterConfig) -> list[str]:
    """Distinct exit countries in subscription order, for the page header."""
    intent = cluster.topology_intent
    if intent is None:
        return []
    countries: list[str] = []
    for exit_ in intent.exits_in_delivery_order():
        if exit_.region and exit_.region not in countries:
            countries.append(exit_.region)
    return countries


def publish_connection_page(
    cluster: ClusterConfig,
    panel: MeridianPanel,
    conn: ServerConnection,
    user: User,
) -> HandoffLinks:
    """Render and upload one user's connection page; never raises for page failures."""
    from meridian.pwa import generate_client_files, mark_connection_page_deployed, upload_client_files

    subscription_url = cluster.panel.subscription_url(user.short_uuid)
    target = page_url(cluster, user)
    if not subscription_url or not target:
        return HandoffLinks(user.username, subscription_url, error="the connection-page path is not configured")
    try:
        document = panel.fetch_subscription(user.short_uuid)
    except RemnawaveError as exc:
        return HandoffLinks(user.username, subscription_url, error=f"could not read the subscription: {exc}")
    links = subscription_protocol_urls(document.content)
    if not links:
        return HandoffLinks(user.username, subscription_url, error="the subscription has no connection links yet")

    host = urlsplit(cluster.panel.url).hostname or ""
    files = generate_client_files(
        links,
        server_ip=cluster.panel.server_ip,
        domain="" if host == cluster.panel.server_ip else host,
        client_name=user.username,
        server_name=cluster.branding.server_name,
        server_icon=cluster.branding.icon,
        color=cluster.branding.color,
        subscription_url=subscription_url,
        exit_countries=exit_countries(cluster),
    )
    error = upload_client_files(conn, user.vless_uuid, files)
    if error:
        logger.warning("Could not upload connection page for %s: %s", user.username, error)
        return HandoffLinks(user.username, subscription_url, error=error)
    mark_connection_page_deployed(cluster, user.vless_uuid)
    return HandoffLinks(user.username, subscription_url, page_url=target)


def control_connection(cluster: ClusterConfig) -> ServerConnection:
    """Open the control server through its saved profile (local when Meridian runs on it)."""
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.resolve import connection_for_entry
    from meridian.servers import ServerRegistry

    entry = ServerRegistry(SERVER_PROFILES_FILE).find(cluster.panel.server_ip)
    if entry is not None:
        return connection_for_entry(entry)
    return ServerConnection(
        cluster.panel.server_ip,
        user=cluster.panel.ssh_user or "root",
        port=cluster.panel.ssh_port or 22,
    )


def remove_connection_page(cluster: ClusterConfig, vless_uuid: str) -> str:
    """Delete a removed user's page from the control server; return an error or ''."""
    from uuid import UUID

    from meridian.pwa import forget_connection_page

    try:
        canonical = str(UUID(vless_uuid))
    except ValueError:
        return f"unexpected page id {vless_uuid!r}"
    with control_connection(cluster) as conn:
        result = conn.run(f"rm -rf /var/www/private/{canonical}", timeout=30)
    if result.returncode != 0:
        return (result.stderr or result.stdout).strip()[:200] or "could not delete the page"
    forget_connection_page(cluster, canonical)
    return ""


def publish_connection_pages(cluster: ClusterConfig, usernames: Iterable[str]) -> list[HandoffLinks]:
    """Publish pages for the given users from the control server."""
    names = list(usernames)
    if not names:
        return []
    with (
        MeridianPanel(cluster.panel.url, cluster.panel.api_token) as panel,
        control_connection(cluster) as conn,
    ):
        results: list[HandoffLinks] = []
        for name in names:
            user = panel.get_user(name)
            if user is None:
                results.append(HandoffLinks(name, error="the user does not exist in Remnawave"))
                continue
            results.append(publish_connection_page(cluster, panel, conn, user))
        return results
