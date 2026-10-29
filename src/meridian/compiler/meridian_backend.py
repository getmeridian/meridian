"""Meridian control-service backend: the walking skeleton's resource kinds (#95).

The Remnawave compile already places ports, nginx routes, certificates, and
firewall rules. For the skeleton this module keeps those server-side kinds and
replaces the panel-shaped ones: the control plane becomes `control_service`, an
exit's Profile, Inbounds, binding, and node become one `agent_runtime` plus one
`node_workload`, primary Hosts become `client_endpoint`s, and users become
`control_user`s. Squads, templates, node-API firewall rules, and panel probes
have no equivalent. Once Remnawave is gone the compiler emits these directly.
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
    FirewallRulePayload,
    HostPayload,
    NodeRuntimePayload,
    NodeWorkloadPayload,
    ResourcePayload,
    ResourcePostcondition,
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

    Exits (bare-IP or domain, WSS, self-steal, egress overrides, WARP), Realm relays, and a
    control domain run; their nginx, certificates, and firewall rules are shared server kinds,
    and WARP installs over SSH with the agent (`AgentRuntimePayload.warp`).
    """
    unsupported = [
        name
        for name, present in (
            ("routing gateways", bool(intent.routing_gateways)),
            ("egress pools", bool(intent.egress_pools)),
            ("routes", bool(intent.routes)),
            ("client IP forwarding", intent.forward_client_ips),
        )
        if present
    ]
    if not any(exit_.server_ref == intent.control.server_ref for exit_ in intent.exits):
        # The control bootstrap takes its nginx Reality route from the exit beside it.
        unsupported.append("a control server without an exit")
    if unsupported:
        raise TopologyCompileError("Meridian's control service does not support " + ", ".join(unsupported) + " yet.")


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
    """Kinds with no Meridian equivalent: squads, templates, panel probes, the node-API rule."""
    if payload.kind in {"internal_squad", "external_squad", "subscription_template", "probe"}:
        return True
    if isinstance(payload, FirewallRulePayload):
        node_ports = {item.payload.api_port for item in old.values() if isinstance(item.payload, NodeRuntimePayload)}
        return bool(payload.source_server_refs) and payload.port in node_ports
    return False


def _workload_of(host: HostPayload, old: dict[str, CompiledResource]) -> str:
    runtime = old[host.node_ref].payload
    if not isinstance(runtime, NodeRuntimePayload):
        raise TopologyCompileError(f"Host {host.remark!r} is not served by an exit node.")
    return runtime.workload_ref


def _rewrite(dependencies: list[str], renamed: dict[str, str | None], owner: str) -> list[str]:
    rewritten = {renamed.get(dep, dep) for dep in dependencies}
    return sorted(dep for dep in rewritten if dep is not None and dep != owner)


def _exists(logical_id: str) -> list[ResourcePostcondition]:
    return [ResourcePostcondition(kind="exists", target_ref=logical_id)]


def _connected(logical_id: str) -> list[ResourcePostcondition]:
    return [*_exists(logical_id), ResourcePostcondition(kind="connected", target_ref=logical_id)]
