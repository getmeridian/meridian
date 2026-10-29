"""Meridian control-service backend: the walking skeleton's resource kinds (#95).

The Remnawave compile already places ports, nginx routes, certificates, and
firewall rules. For the skeleton this module keeps those server-side kinds and
replaces the panel-shaped ones: the control plane becomes `control_service`, an
exit's or routing gateway's Profile, Inbounds, binding, and node become one
`agent_runtime` plus one `node_workload` (a gateway's pools, routes, and gateway
resource fold into it), primary Hosts become `client_endpoint`s, and users become
`control_user`s. Squads, templates, service users, node-API firewall rules, and
panel probes have no equivalent. Once Remnawave is gone the compiler emits these directly.
"""

from __future__ import annotations

from meridian.compiler.builder import PlanBuilder
from meridian.compiler.client_delivery import ALT_FINGERPRINT_TAG
from meridian.compiler.errors import TopologyCompileError
from meridian.compiler.models import (
    AccessUserPayload,
    AgentRuntimePayload,
    CertificatePayload,
    ClientEndpointPayload,
    CompiledResource,
    ConfigProfilePayload,
    ControlPlaneRuntimePayload,
    ControlServicePayload,
    ControlUserPayload,
    EgressPoolPayload,
    FirewallRulePayload,
    HostPayload,
    NodeRuntimePayload,
    NodeWorkloadPayload,
    ResourcePayload,
    ResourcePostcondition,
    RouteRulePayload,
    RoutingGatewayPayload,
    ServerBaselinePayload,
    ServiceSettingsPayload,
    SubscriptionSettingsPayload,
    make_resource,
)
from meridian.core.topology import SetupIntent

CONTROL_SERVICE_ID = "control:service"
SERVICE_SETTINGS_ID = "settings:service"


def validate_meridian_backend(intent: SetupIntent) -> None:
    """Refuse what the control service cannot run yet.

    Exits (bare-IP or domain, WSS, self-steal, egress overrides, WARP), Realm relays, routing
    gateways with pools and routes, a control domain, and a control server without an exit (its
    bootstrap falls back to default backend ports, as a panel-only server's does) run; their
    nginx, certificates, and firewall rules are shared server kinds, and WARP installs over SSH
    with the agent (`AgentRuntimePayload.warp`). Client IP forwarding changes the PROXY protocol
    chain in front of every workload and waits for its own review.
    """
    if intent.forward_client_ips:
        raise TopologyCompileError("Meridian's control service does not support client IP forwarding yet.")


def to_meridian_backend(builder: PlanBuilder) -> PlanBuilder:
    """Return a builder whose panel-shaped resources are replaced by Meridian's own kinds."""
    old = builder.resources
    renamed: dict[str, str | None] = {}
    replacements: dict[str, tuple[ResourcePayload, list[str], list[ResourcePostcondition]]] = {}

    def replace(
        logical_id: str,
        payload: ResourcePayload,
        sources: list[str],
        *,
        extra: list[str] | None = None,
        connected: bool = False,
    ) -> None:
        """Replace *sources* by one resource that inherits their outside dependencies."""
        for source in sources:
            renamed[source] = logical_id
        inherited = {dep for source in sources for dep in old[source].dependencies} - set(sources)
        conditions = _connected(logical_id) if connected else _exists(logical_id)
        replacements[logical_id] = (payload, sorted({*inherited, *(extra or [])}), conditions)

    for logical_id, resource in old.items():
        payload = resource.payload
        if isinstance(payload, ControlPlaneRuntimePayload):
            replace(
                CONTROL_SERVICE_ID,
                ControlServicePayload(
                    server_ref=payload.server_ref,
                    public_hostname=payload.public_hostname,
                    internal_https_port=payload.internal_https_port,
                ),
                [logical_id],
            )
        elif isinstance(payload, NodeRuntimePayload):
            exit_id = payload.workload_ref
            profile = old[f"profile:{exit_id}"].payload
            assert isinstance(profile, ConfigProfilePayload)
            agent_id = f"agent:{exit_id}"
            replacements[agent_id] = (
                AgentRuntimePayload(workload_ref=exit_id, server_ref=payload.server_ref, warp=payload.warp),
                [CONTROL_SERVICE_ID, *(dep for dep in resource.dependencies if dep.startswith("baseline:"))],
                _connected(agent_id),
            )
            workload_id = f"workload:{exit_id}"
            replace(
                workload_id,
                NodeWorkloadPayload(
                    workload_ref=exit_id,
                    server_ref=payload.server_ref,
                    agent_ref=agent_id,
                    profile=profile,
                ),
                [f"profile:{exit_id}", f"binding:{exit_id}", logical_id, *profile.inbound_refs],
                extra=[agent_id],
                connected=True,
            )
        elif isinstance(payload, HostPayload):
            # Keep the entries Base64 clients see; the service renders every other format from them.
            if payload.is_hidden or "XRAY_BASE64" in payload.exclude_from_subscription_types:
                renamed[logical_id] = None
                continue
            replace(
                "endpoint:" + logical_id.removeprefix("host:"),
                ClientEndpointPayload(
                    owner_ref=payload.owner_ref,
                    # A relay owns its Host, but the keys belong to the exit whose node serves it.
                    workload_ref=_workload_of(payload, old),
                    remark=payload.remark,
                    protocol=payload.protocol,
                    address_server_ref=payload.address_server_ref,
                    address=payload.address,
                    public_port=payload.public_port,
                    sni=payload.sni,
                    host=payload.host,
                    path=payload.path,
                    fingerprint=payload.fingerprint,
                    position=payload.view_position,
                    fingerprint_copy=ALT_FINGERPRINT_TAG in payload.tags,
                ),
                [logical_id],
            )
        elif isinstance(payload, AccessUserPayload):
            account_id = f"account:{logical_id.removeprefix('user:')}"
            renamed[logical_id] = account_id
            replacements[account_id] = (
                ControlUserPayload(username=payload.username),
                [CONTROL_SERVICE_ID],
                _exists(account_id),
            )
        elif isinstance(payload, SubscriptionSettingsPayload):
            renamed[logical_id] = SERVICE_SETTINGS_ID
            replacements[SERVICE_SETTINGS_ID] = (
                ServiceSettingsPayload(profile_title=payload.profile_title),
                [CONTROL_SERVICE_ID],
                _exists(SERVICE_SETTINGS_ID),
            )
        elif isinstance(payload, RoutingGatewayPayload | EgressPoolPayload | RouteRulePayload):
            # The gateway's node spec carries its edges, balancers, and rules; its agent applies them at once.
            gateway_ref = payload.gateway_id if isinstance(payload, RoutingGatewayPayload) else payload.gateway_ref
            renamed[logical_id] = f"workload:{gateway_ref}"
        elif isinstance(payload, ServerBaselinePayload):
            replacements[logical_id] = (
                payload.model_copy(update={"install_docker": False}),
                list(resource.dependencies),
                list(resource.postconditions),
            )
        elif _panel_only(payload, old):
            renamed[logical_id] = None

    result = PlanBuilder()
    for logical_id, resource in old.items():
        if logical_id in renamed:
            continue
        payload, dependencies, conditions = replacements.get(
            logical_id, (resource.payload, list(resource.dependencies), list(resource.postconditions))
        )
        dependencies = _rewrite(dependencies, renamed, logical_id)
        if isinstance(payload, CertificatePayload):
            # A Hysteria2 workload loads its certificate at startup, so the certificate never waits on it.
            dependencies = [dep for dep in dependencies if not dep.startswith("workload:")]
        result.resources[logical_id] = make_resource(
            logical_id,
            payload,
            dependencies=dependencies,
            postconditions=conditions,
        )
    for logical_id, (payload, dependencies, conditions) in replacements.items():
        if logical_id in old:
            continue
        result.resources[logical_id] = make_resource(
            logical_id,
            payload,
            dependencies=_rewrite(dependencies, renamed, logical_id),
            postconditions=conditions,
        )
    return result


def _panel_only(payload: ResourcePayload, old: dict[str, CompiledResource]) -> bool:
    """Kinds with no Meridian equivalent: squads, templates, panel probes, the node-API rule.

    A gateway's service users go too: each bridge's one client ID derives from its exit's keys.
    """
    if payload.kind in {"internal_squad", "external_squad", "subscription_template", "probe", "service_user"}:
        return True
    if isinstance(payload, FirewallRulePayload):
        # Exactly a node's API rule: its server and port, opened to the control server alone. A relay
        # hop listening on the same port elsewhere, or from another hop, keeps its rule.
        control = {
            item.payload.server_ref for item in old.values() if isinstance(item.payload, ControlPlaneRuntimePayload)
        }
        node_apis = {
            (item.payload.server_ref, item.payload.api_port)
            for item in old.values()
            if isinstance(item.payload, NodeRuntimePayload)
        }
        return (
            payload.transport == "tcp"
            and (payload.server_ref, payload.port) in node_apis
            and len(payload.source_server_refs) == 1
            and payload.source_server_refs[0] in control
        )
    return False


def _workload_of(host: HostPayload, old: dict[str, CompiledResource]) -> str:
    runtime = old[host.node_ref].payload
    if not isinstance(runtime, NodeRuntimePayload):
        raise TopologyCompileError(f"Host {host.remark!r} is not served by a node.")
    return runtime.workload_ref


def _rewrite(dependencies: list[str], renamed: dict[str, str | None], owner: str) -> list[str]:
    rewritten = {renamed.get(dep, dep) for dep in dependencies}
    return sorted(dep for dep in rewritten if dep is not None and dep != owner)


def _exists(logical_id: str) -> list[ResourcePostcondition]:
    return [ResourcePostcondition(kind="exists", target_ref=logical_id)]


def _connected(logical_id: str) -> list[ResourcePostcondition]:
    return [*_exists(logical_id), ResourcePostcondition(kind="connected", target_ref=logical_id)]
