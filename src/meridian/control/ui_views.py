"""Read-only views the web UI needs beyond the admin API: servers, declared people, and share links.

The deployer's fleet file stays the authority on topology; the service knows it
only through the secret-free intent of the last applied revision, which names each
node's role and the people the CLI declares. Nothing here changes state.
"""

from __future__ import annotations

from typing import Any, Literal

import segno

from meridian.control.service import ControlService
from meridian.control.ui_models import FleetView, ServerView, ShareView
from meridian.core.attention import AttentionThresholds, derive_server_attention

_QR_DARK = "#1A1D2E"
_QR_LIGHT = "#FFFFFF"


def qr_data_uri(text: str) -> str:
    """`text` as an SVG QR code in a `data:` URI (the UI's CSP allows `img-src data:`)."""
    return str(segno.make(text, error="m").svg_data_uri(scale=6, border=2, dark=_QR_DARK, light=_QR_LIGHT))


def applied_intent(service: ControlService) -> tuple[int | None, dict[str, Any]]:
    """The newest applied revision's number and intent, or (None, {}) before the first."""
    view = service.people.intent()
    return view.revision, view.intent


def declared_users(intent: dict[str, Any]) -> list[str]:
    """The people an applied intent declares: the deployer's CLI owns their existence and links."""
    users = _access(intent).get("users")
    return [name for name in users if isinstance(name, str)] if isinstance(users, list) else []


def is_declared(service: ControlService, username: str) -> bool:
    """Declared by the newest applied revision or by any revision after it.

    An apply creates its people while its revision is still approved, so the applied
    intent alone would leave them to the console until the apply finished; one that
    failed elsewhere has still created them, and the CLI's retry reuses their links.
    A merely proposed revision declares no one: a `propose` token must not lock the
    owner out of their own people.
    """
    return username in service.people.intent().declared


def fleet_view(service: ControlService) -> FleetView:
    number, intent = applied_intent(service)
    roles: dict[str, tuple[Literal["exit", "gateway", "node"], str]] = {}
    for exit_ in _items(intent, "exits"):
        roles[str(exit_.get("id", ""))] = ("exit", str(exit_.get("region") or ""))
    for gateway in _items(intent, "routing_gateways"):
        roles[str(gateway.get("id", ""))] = ("gateway", str(gateway.get("region") or ""))
    declared = declared_users(intent)
    names = _access(intent).get("display_names", {})
    display_names = {str(k): str(v) for k, v in names.items()} if isinstance(names, dict) else {}
    traffic = service.traffic.node_totals()
    servers: list[ServerView] = []
    for status in service.list_nodes():
        role, region = roles.get(status.node_id, ("node", ""))
        applied = status.applied.revision if status.applied else None
        servers.append(
            ServerView(
                node_id=status.node_id,
                role=role,
                region=region,
                live=status.is_live(),
                last_seen_at=status.last_seen_at,
                agent_version=status.agent_version,
                revision=status.revision,
                applied_revision=applied,
                in_sync=status.in_sync,
                failed=list(status.failed),
                errors=list(status.errors),
                traffic_bytes=traffic.get(status.node_id, 0),
                runtime=status.runtime,
                runtime_available=status.runtime_available(),
                **status.traffic_evidence().model_dump(),
            )
        )
    settings = service.settings()
    usage = service.server_usage()
    return FleetView(
        server_usage=usage.servers,
        server_attention=derive_server_attention(usage.servers, AttentionThresholds(), usage.observed_at),
        profile_title=settings.profile_title,
        public_url=settings.public_url,
        declared_users=declared,
        display_names=display_names,
        intent_revision=number,
        servers=servers,
    )


def share_view(service: ControlService, username: str) -> ShareView:
    """Every person's subscription is also their connection page, including console invitees."""
    connection_view = service.user_connections(username)
    declared = is_declared(service, username)
    subscription = connection_view.subscription_url
    return ShareView(
        username=username,
        declared=declared,
        subscription_url=subscription,
        page_url=subscription,
        qr_svg=qr_data_uri(subscription) if subscription else "",
        profile_title=connection_view.profile_title,
        connections=[
            connection.model_copy(update={"qr_svg": qr_data_uri(connection.uri)})
            for connection in connection_view.connections
        ],
        formats=connection_view.formats,
    )


def _access(intent: dict[str, Any]) -> dict[str, Any]:
    access = intent.get("access")
    return access if isinstance(access, dict) else {}


def _items(intent: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = intent.get(key)
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
