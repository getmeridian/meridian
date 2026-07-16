"""Compile and attach one common preparation resource per topology server."""

from __future__ import annotations

from meridian.compiler.builder import PlanBuilder
from meridian.compiler.firewalls import stable_token
from meridian.compiler.models import (
    CertificatePayload,
    ControlPlaneRuntimePayload,
    FirewallRulePayload,
    NginxArtifactPayload,
    NodeRuntimePayload,
    RealmHopPayload,
    ServerBaselinePayload,
    make_resource,
)
from meridian.core.topology import SetupIntent

_SERVER_MUTATIONS = (
    CertificatePayload,
    ControlPlaneRuntimePayload,
    FirewallRulePayload,
    NginxArtifactPayload,
    NodeRuntimePayload,
    RealmHopPayload,
)


def compile_server_baselines(
    builder: PlanBuilder,
    intent: SetupIntent,
) -> dict[str, str]:
    """Add stable baseline resources and return server-ref to logical-ID map."""
    docker_servers = {
        intent.control.server_ref,
        *(exit_.server_ref for exit_ in intent.exits),
        *(gateway.server_ref for gateway in intent.routing_gateways),
    }
    all_servers = {
        *docker_servers,
        *(server_ref for relay in intent.transparent_relays for server_ref in relay.hop_server_refs),
    }
    baseline_ids: dict[str, str] = {}
    for server_ref in sorted(all_servers):
        logical_id = f"baseline:{stable_token(server_ref)}"
        baseline_ids[server_ref] = builder.add(
            logical_id,
            ServerBaselinePayload(
                server_ref=server_ref,
                install_docker=server_ref in docker_servers,
            ),
        )
    return baseline_ids


def attach_server_baselines(
    builder: PlanBuilder,
    baseline_ids: dict[str, str],
) -> None:
    """Make every server mutation wait for its host baseline."""
    for logical_id, resource in list(builder.resources.items()):
        payload = resource.payload
        if not isinstance(payload, _SERVER_MUTATIONS):
            continue
        baseline_id = baseline_ids[payload.server_ref]
        builder.resources[logical_id] = make_resource(
            logical_id,
            payload,
            dependencies=[*resource.dependencies, baseline_id],
            postconditions=resource.postconditions,
        )
