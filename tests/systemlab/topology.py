"""Deterministic V4 topology exercised by the Docker system lab."""

from __future__ import annotations

from collections.abc import Mapping

from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    DeliveryIntent,
    EgressPoolIntent,
    ExitIntent,
    OrderedRouteIntent,
    ProtocolPathIntent,
    RoutingGatewayIntent,
    SetupIntent,
    TransparentRelayIntent,
)


def build_systemlab_intent(
    server_refs: Mapping[str, str],
) -> SetupIntent:
    """Build two exits, a Realm chain, and routed failover."""
    return SetupIntent(
        control=ControlPlaneIntent(
            server_ref=server_refs["exit_a"],
            title="Meridian system lab",
        ),
        exits=[
            ExitIntent(
                id="exit-a",
                server_ref=server_refs["exit_a"],
                paths=[
                    ProtocolPathIntent(
                        id="exit-a-reality",
                        protocol="reality",
                        reality_sni="www.google.com",
                    )
                ],
            ),
            ExitIntent(
                id="exit-b",
                server_ref=server_refs["exit_b"],
                paths=[
                    ProtocolPathIntent(
                        id="exit-b-reality",
                        protocol="reality",
                        reality_sni="www.google.com",
                    )
                ],
            ),
        ],
        transparent_relays=[
            TransparentRelayIntent(
                id="relay-chain",
                hop_server_refs=[
                    server_refs["relay_a"],
                    server_refs["relay_b"],
                ],
                exit_ref="exit-a",
                protocol_path_ref="exit-a-reality",
                listen_port=443,
                reality_sni="www.cloudflare.com",
            )
        ],
        routing_gateways=[
            RoutingGatewayIntent(
                id="gateway-a",
                server_ref=server_refs["gateway"],
                bridge_path_ref="exit-a-reality",
            )
        ],
        egress_pools=[
            EgressPoolIntent(
                id="pool-a",
                exit_refs=["exit-a", "exit-b"],
            )
        ],
        routes=[
            OrderedRouteIntent(
                id="route-probe-via-pool",
                priority=10,
                match="domain",
                match_values=["ifconfig.me"],
                target_ref="pool-a",
                source_gateway_ref="gateway-a",
            ),
            OrderedRouteIntent(
                id="route-default-pool",
                priority=100,
                match="all",
                target_ref="pool-a",
                source_gateway_ref="gateway-a",
            ),
        ],
        default_egress_ref="pool-a",
        access=AccessIntent(users=["acceptance"]),
        delivery=DeliveryIntent(
            profile_title="Meridian system lab",
            template_name="meridian-system-lab",
            formats=["base64", "xray_json", "mihomo"],
        ),
    )
