"""Credential-bearing connection views, composed from the service's canonical subscription."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast
from urllib.parse import unquote, urlsplit

from meridian.control.errors import ServiceError
from meridian.control.store import Store
from meridian.core.clients import ClientConnection, ClientConnections, SubscriptionFormat, SubscriptionFormatUrl
from meridian.core.topology import ProtocolKind
from meridian.subscription_links import protocol_key

if TYPE_CHECKING:
    from meridian.control.service import ControlService

FORMATS: tuple[SubscriptionFormat, ...] = ("xray-json", "mihomo", "singbox")


def read_connections(store: Store, service: ControlService, username: str) -> ClientConnections:
    """Read without registering a device or persisting another copy of the credentials."""
    with store.lock:
        record = service.get_user(username)
        if record is None:
            raise ServiceError("not_found", "Unknown user.", 404)
        response = service.subscription(record.sub_token, "info")
        info = json.loads(response.body)
        title = service.settings().profile_title
    subscription = info["subscriptionUrl"]
    connections = []
    for uri in info["links"]:
        protocol = cast(ProtocolKind | None, protocol_key(uri) or None)
        connections.append(
            ClientConnection(
                remark=unquote(urlsplit(uri).fragment),
                protocol=protocol,
                available=record.status == "active" and protocol is not None,
                uri=uri,
            )
        )
    return ClientConnections(
        username=username,
        status=record.status,
        profile_title=title,
        subscription_url=subscription,
        connections=connections,
        formats=[SubscriptionFormatUrl(format=name, url=f"{subscription}/{name}") for name in FORMATS]
        if subscription
        else [],
    )
