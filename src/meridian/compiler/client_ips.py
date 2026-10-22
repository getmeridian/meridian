"""Pass each client's real address through nginx when the fleet opts in.

Every TCP connection reaches Xray and the panel through nginx, so without this
they all appear to come from 127.0.0.1. When ``forward_client_ips`` is on, the
443 streams send the PROXY protocol header, TLS servers read it and set
X-Forwarded-For, and every nginx-fronted inbound trusts what it receives.
Reality passes the header on to a self-steal fallback server (``xver``).
Hysteria2 is direct UDP and always sees the real address.
"""

from __future__ import annotations

from meridian.compiler.allocations import PortAllocator
from meridian.compiler.builder import PlanBuilder
from meridian.compiler.models import (
    ConfigProfilePayload,
    InboundPayload,
    NginxArtifactPayload,
    ResourcePayload,
    make_resource,
)
from meridian.core.defaults import CONTROL_PROXY_TLS_PORT, CONTROL_TLS_PORT

_FORWARDED_PROTOCOLS = frozenset({"reality", "xhttp", "wss"})


def forward_client_ips(builder: PlanBuilder, ports: PortAllocator) -> None:
    """Mark every nginx hop and nginx-fronted inbound to carry the client's address."""
    for logical_id, resource in list(builder.resources.items()):
        payload = resource.payload
        if isinstance(payload, NginxArtifactPayload):
            updated: ResourcePayload = _forwarding_artifact(logical_id, payload, ports)
        elif isinstance(payload, InboundPayload):
            updated = _trusting_inbound(payload)
        elif isinstance(payload, ConfigProfilePayload):
            updated = payload.model_copy(update={"inbounds": [_trusting_inbound(item) for item in payload.inbounds]})
        else:
            continue
        builder.resources[logical_id] = make_resource(
            logical_id,
            updated,
            dependencies=resource.dependencies,
            postconditions=resource.postconditions,
        )


def _forwarding_artifact(logical_id: str, payload: NginxArtifactPayload, ports: PortAllocator) -> NginxArtifactPayload:
    update: dict[str, object] = {"forward_client_ip": True}
    if payload.layer == "stream":
        # The panel's second listener reads the header; the plain one stays for direct use.
        update["routes"] = [
            route.model_copy(update={"backend_port": CONTROL_PROXY_TLS_PORT})
            if route.backend_port == CONTROL_TLS_PORT and route.backend_server_ref == payload.server_ref
            else route
            for route in payload.routes
        ]
        if payload.fallback_server_name:
            update["fallback_strip_port"] = ports.auxiliary_tcp(
                payload.server_ref, f"strip:{logical_id}", base=19000, size=1000
            )
    return payload.model_copy(update=update)


def _trusting_inbound(inbound: InboundPayload) -> InboundPayload:
    update: dict[str, object] = {}
    # A self-steal fallback is an nginx TLS server, which now reads a PROXY header.
    if inbound.reality_dest_port:
        update["reality_dest_proxy"] = True
    # Inbounds with an explicit listen address (gateway bridges) are dialed directly, not through nginx.
    if inbound.protocol in _FORWARDED_PROTOCOLS and not inbound.listen_address:
        update["trust_forwarded_ip"] = True
    return inbound.model_copy(update=update) if update else inbound
