"""Pure finite compiler from setup intent to reviewed resources."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

from meridian.compiler.allocations import PortAllocator
from meridian.compiler.errors import TopologyCompileError
from meridian.compiler.models import (
    AccessUserPayload,
    CertificatePayload,
    CompiledResource,
    ConfigProfilePayload,
    ControlPlaneRuntimePayload,
    EgressPoolPayload,
    FirewallRulePayload,
    HostPayload,
    InboundPayload,
    InternalSquadPayload,
    NginxArtifactPayload,
    NginxRouteSpec,
    NodeBindingPayload,
    NodeRuntimePayload,
    ProbePayload,
    RealmHopPayload,
    ResourcePayload,
    ResourcePlan,
    ResourcePostcondition,
    RouteRulePayload,
    RoutingGatewayPayload,
    ServiceUserPayload,
    SubscriptionSettingsPayload,
    SubscriptionTemplatePayload,
    canonical_hash,
    compute_plan_hash,
    make_resource,
)
from meridian.core.topology import ExitIntent, ProtocolPathIntent, SetupIntent


@dataclass
class _PlanBuilder:
    resources: dict[str, CompiledResource] = field(default_factory=dict)

    def add(
        self,
        logical_id: str,
        payload: ResourcePayload,
        *,
        dependencies: list[str] | None = None,
        postconditions: list[ResourcePostcondition] | None = None,
    ) -> str:
        if logical_id in self.resources:
            raise TopologyCompileError(f"Compiler produced duplicate logical resource {logical_id}.")
        self.resources[logical_id] = make_resource(
            logical_id,
            payload,
            dependencies=dependencies,
            postconditions=postconditions or [_exists(logical_id)],
        )
        return logical_id

    def ordered(self) -> list[CompiledResource]:
        pending = dict(self.resources)
        emitted: list[CompiledResource] = []
        emitted_ids: set[str] = set()
        while pending:
            ready = sorted(
                logical_id
                for logical_id, resource in pending.items()
                if set(resource.dependencies) <= emitted_ids
            )
            if not ready:
                unresolved = ", ".join(sorted(pending))
                raise TopologyCompileError(
                    f"Compiled dependency graph contains a cycle or missing reference: {unresolved}."
                )
            for logical_id in ready:
                emitted.append(pending.pop(logical_id))
                emitted_ids.add(logical_id)
        return emitted


def compile_topology(intent: SetupIntent) -> ResourcePlan:
    """Compile complete setup intent without network, filesystem, or randomness."""
    builder = _PlanBuilder()
    ports = PortAllocator()
    control_id = builder.add(
        "control:runtime",
        ControlPlaneRuntimePayload(
            server_ref=intent.control.server_ref,
            public_hostname=intent.control.public_hostname,
        ),
        postconditions=[
            _exists("control:runtime"),
            ResourcePostcondition(kind="listening", target_ref="control:runtime", detail="HTTPS control plane"),
        ],
    )
    _add_firewall(builder, intent.control.server_ref, "tcp", 80)
    _add_firewall(builder, intent.control.server_ref, "tcp", 443)

    inbound_by_path: dict[tuple[str, str], str] = {}
    runtime_by_exit: dict[str, str] = {}
    allocated_path_ports: dict[tuple[str, str], int] = {}
    stream_routes: dict[tuple[str, int], list[NginxRouteSpec]] = defaultdict(list)
    stream_dependencies: dict[tuple[str, int], set[str]] = defaultdict(set)
    stream_targets: dict[tuple[str, int, str], tuple[str, int]] = {}
    direct_host_ids: list[str] = []
    reality_names_by_path = _reality_names_by_path(intent)

    exit_servers = [exit_.server_ref for exit_ in intent.exits]
    if len(exit_servers) != len(set(exit_servers)):
        raise TopologyCompileError(
            "Each V4 exit workload requires its own server because one Remnawave node runtime "
            "can activate only one config profile."
        )

    for exit_ in sorted(intent.exits, key=lambda item: item.id):
        profile_id = f"profile:{exit_.id}"
        sorted_paths = sorted(exit_.paths, key=lambda item: item.id)
        path_refs = [f"inbound:{exit_.id}:{path.id}" for path in sorted_paths]
        inbound_specs: list[InboundPayload] = []
        for path in sorted_paths:
            if path.protocol == "hysteria2" and path.listen_port and path.listen_port != path.public_port:
                raise TopologyCompileError(
                    f"Hysteria2 path {exit_.id}/{path.id} must listen on its public UDP port."
                )
            inbound_id = f"inbound:{exit_.id}:{path.id}"
            listen_port = ports.path_port(exit_, path)
            allocated_path_ports[(exit_.id, path.id)] = listen_port
            inbound_by_path[(exit_.id, path.id)] = inbound_id
            inbound_specs.append(
                InboundPayload(
                    workload_ref=exit_.id,
                    protocol=path.protocol,
                    tag=f"meridian-{exit_.id}-{path.protocol}",
                    listen_port=listen_port,
                    public_port=path.public_port,
                    reality_sni=path.reality_sni,
                    reality_server_names=reality_names_by_path.get((exit_.id, path.id), []),
                    tls_sni=path.tls_sni,
                    host=path.host,
                    path=path.path,
                )
            )
        builder.add(
            profile_id,
            ConfigProfilePayload(
                workload_id=exit_.id,
                name=f"Meridian v4 / {exit_.id}",
                inbound_refs=path_refs,
                inbounds=inbound_specs,
                outbound_tags=["warp"] if exit_.warp else ["direct"],
            ),
            dependencies=[control_id],
        )
        for path, inbound_spec in zip(sorted_paths, inbound_specs, strict=True):
            inbound_id = f"inbound:{exit_.id}:{path.id}"
            builder.add(
                inbound_id,
                inbound_spec,
                dependencies=[profile_id],
            )

        binding_id = builder.add(
            f"binding:{exit_.id}",
            NodeBindingPayload(
                workload_ref=exit_.id,
                server_ref=exit_.server_ref,
                name=f"Meridian v4 / {exit_.id}",
                profile_ref=profile_id,
                inbound_refs=path_refs,
            ),
            dependencies=[profile_id, *path_refs],
        )
        runtime_id = builder.add(
            f"node:{exit_.id}",
            NodeRuntimePayload(
                workload_ref=exit_.id,
                server_ref=exit_.server_ref,
                binding_ref=binding_id,
                warp=exit_.warp,
            ),
            dependencies=[binding_id],
            postconditions=[
                _exists(f"node:{exit_.id}"),
                ResourcePostcondition(kind="connected", target_ref=f"node:{exit_.id}"),
            ],
        )
        runtime_by_exit[exit_.id] = runtime_id
        node_api_port = NodeRuntimePayload(
            workload_ref=exit_.id,
            server_ref=exit_.server_ref,
            binding_ref=binding_id,
            warp=exit_.warp,
        ).api_port
        _add_firewall(
            builder,
            exit_.server_ref,
            "tcp",
            node_api_port,
            source_server_refs=[intent.control.server_ref],
        )

        _compile_exit_endpoints(
            builder=builder,
            ports=ports,
            exit_=exit_,
            runtime_id=runtime_id,
            inbound_by_path=inbound_by_path,
            allocated_path_ports=allocated_path_ports,
            stream_routes=stream_routes,
            stream_dependencies=stream_dependencies,
            stream_targets=stream_targets,
            direct_host_ids=direct_host_ids,
        )

    stream_ids = _compile_stream_artifacts(builder, stream_routes, stream_dependencies)
    _attach_stream_dependencies(builder, direct_host_ids, stream_ids)

    relay_host_ids = _compile_relays(
        builder=builder,
        intent=intent,
        runtime_by_exit=runtime_by_exit,
        inbound_by_path=inbound_by_path,
        stream_ids=stream_ids,
    )

    all_inbounds = sorted(inbound_by_path.values())
    squad_id = builder.add(
        "squad:access",
        InternalSquadPayload(name=intent.access.squad_name, inbound_refs=all_inbounds),
        dependencies=all_inbounds,
    )
    user_ids = [
        builder.add(
            f"user:{username}",
            AccessUserPayload(username=username, squad_ref=squad_id),
            dependencies=[squad_id],
        )
        for username in sorted(intent.access.users)
    ]
    template_id = builder.add(
        f"template:{intent.delivery.template_name}",
        SubscriptionTemplatePayload(
            name=intent.delivery.template_name,
            profile_title=intent.delivery.profile_title,
            formats=intent.delivery.formats,
        ),
        dependencies=[control_id],
    )
    settings_id = builder.add(
        "subscription:settings",
        SubscriptionSettingsPayload(template_ref=template_id),
        dependencies=[template_id],
    )

    gateway_ids = _compile_routing(
        builder=builder,
        intent=intent,
        control_id=control_id,
        squad_id=squad_id,
        runtime_by_exit=runtime_by_exit,
    )
    _compile_probes(
        builder=builder,
        runtime_by_exit=runtime_by_exit,
        host_ids=[*direct_host_ids, *relay_host_ids],
        user_ids=user_ids,
        settings_id=settings_id,
        gateway_ids=gateway_ids,
    )

    ordered = builder.ordered()
    intent_hash = canonical_hash(_normalized_intent(intent))
    plan_hash = compute_plan_hash(compiler_version="v4", intent_hash=intent_hash, resources=ordered)
    return ResourcePlan(intent_hash=intent_hash, plan_hash=plan_hash, resources=ordered)


def _compile_exit_endpoints(
    *,
    builder: _PlanBuilder,
    ports: PortAllocator,
    exit_: ExitIntent,
    runtime_id: str,
    inbound_by_path: dict[tuple[str, str], str],
    allocated_path_ports: dict[tuple[str, str], int],
    stream_routes: dict[tuple[str, int], list[NginxRouteSpec]],
    stream_dependencies: dict[tuple[str, int], set[str]],
    stream_targets: dict[tuple[str, int, str], tuple[str, int]],
    direct_host_ids: list[str],
) -> None:
    tls_groups: dict[tuple[str, int], list[ProtocolPathIntent]] = defaultdict(list)
    direct_dependencies: dict[str, list[str]] = defaultdict(list)
    for path in sorted(exit_.paths, key=lambda item: item.id):
        inbound_id = inbound_by_path[(exit_.id, path.id)]
        backend_port = allocated_path_ports[(exit_.id, path.id)]
        if path.protocol == "hysteria2":
            firewall_id = _add_firewall(builder, exit_.server_ref, "udp", path.public_port)
            certificate_id = _ensure_certificate(
                builder,
                exit_id=exit_.id,
                server_ref=exit_.server_ref,
                hostname=path.tls_sni,
                runtime_id=runtime_id,
            )
            direct_dependencies[path.id].extend([certificate_id, firewall_id])
        elif path.protocol == "reality":
            stream_key = (exit_.server_ref, path.public_port)
            inbound = builder.resources[inbound_id].payload
            assert isinstance(inbound, InboundPayload)
            for server_name in inbound.reality_server_names or [path.reality_sni]:
                _reserve_stream_name(
                    stream_targets,
                    server_ref=exit_.server_ref,
                    public_port=path.public_port,
                    server_name=server_name,
                    target=(exit_.server_ref, backend_port),
                )
                stream_routes[stream_key].append(
                    NginxRouteSpec(
                        match="sni",
                        server_names=[server_name],
                        backend_server_ref=exit_.server_ref,
                        backend_port=backend_port,
                        protocol="reality",
                    )
                )
            stream_dependencies[stream_key].update({runtime_id, inbound_id})
            _add_firewall(builder, exit_.server_ref, "tcp", path.public_port)
        else:
            tls_groups[(path.tls_sni, path.public_port)].append(path)

    for (tls_sni, public_port), paths in sorted(tls_groups.items()):
        certificate_id = _ensure_certificate(
            builder,
            exit_id=exit_.id,
            server_ref=exit_.server_ref,
            hostname=tls_sni,
            runtime_id=runtime_id,
        )
        tls_port = ports.auxiliary_tcp(
            exit_.server_ref,
            f"tls:{exit_.id}:{tls_sni}:{public_port}",
            base=18000,
            size=1000,
        )
        http_id = f"nginx:{exit_.id}:{_token(tls_sni)}:http"
        http_routes = [
            NginxRouteSpec(
                match="host_path",
                server_names=[path.host],
                path=path.path,
                backend_server_ref=exit_.server_ref,
                backend_port=allocated_path_ports[(exit_.id, path.id)],
                protocol=path.protocol,
            )
            for path in sorted(paths, key=lambda item: item.id)
        ]
        inbound_ids = [inbound_by_path[(exit_.id, path.id)] for path in paths]
        builder.add(
            http_id,
            NginxArtifactPayload(
                server_ref=exit_.server_ref,
                listener_port=tls_port,
                layer="http",
                tls_hostname=tls_sni,
                routes=http_routes,
            ),
            dependencies=[certificate_id, runtime_id, *inbound_ids],
            postconditions=[
                _exists(http_id),
                ResourcePostcondition(kind="listening", target_ref=http_id, detail=str(tls_port)),
            ],
        )
        stream_key = (exit_.server_ref, public_port)
        _reserve_stream_name(
            stream_targets,
            server_ref=exit_.server_ref,
            public_port=public_port,
            server_name=tls_sni,
            target=(exit_.server_ref, tls_port),
        )
        stream_routes[stream_key].append(
            NginxRouteSpec(
                match="sni",
                server_names=[tls_sni],
                backend_server_ref=exit_.server_ref,
                backend_port=tls_port,
            )
        )
        stream_dependencies[stream_key].add(http_id)
        _add_firewall(builder, exit_.server_ref, "tcp", public_port)

    for path in sorted(exit_.paths, key=lambda item: item.id):
        inbound_id = inbound_by_path[(exit_.id, path.id)]
        host_id = f"host:{exit_.id}:{path.id}:direct"
        dependencies = [runtime_id, inbound_id, *direct_dependencies[path.id]]
        is_tls = path.protocol in {"xhttp", "wss", "hysteria2"}
        direct_host_ids.append(
            builder.add(
                host_id,
                HostPayload(
                    owner_ref=exit_.id,
                    remark=f"Meridian v4 / {exit_.id} / {path.id} / direct",
                    node_ref=runtime_id,
                    inbound_ref=inbound_id,
                    address_server_ref=exit_.server_ref,
                    address=(path.host or path.tls_sni) if is_tls else "",
                    public_port=path.public_port,
                    protocol=path.protocol,
                    sni=path.reality_sni or path.tls_sni,
                    host=path.host,
                    path=f"/{path.path}" if path.path else "",
                    alpn="h3" if path.protocol == "hysteria2" else "",
                    fingerprint="chrome" if path.protocol == "reality" else "",
                    security_layer="TLS" if is_tls else "DEFAULT",
                ),
                dependencies=dependencies,
            )
        )


def _compile_stream_artifacts(
    builder: _PlanBuilder,
    routes: dict[tuple[str, int], list[NginxRouteSpec]],
    dependencies: dict[tuple[str, int], set[str]],
) -> dict[tuple[str, int], str]:
    stream_ids: dict[tuple[str, int], str] = {}
    for (server_ref, port), route_specs in sorted(routes.items()):
        logical_id = _stream_id(server_ref, port)
        builder.add(
            logical_id,
            NginxArtifactPayload(
                server_ref=server_ref,
                listener_port=port,
                layer="stream",
                routes=sorted(
                    route_specs,
                    key=lambda route: (route.server_names, route.backend_server_ref, route.backend_port),
                ),
            ),
            dependencies=sorted(dependencies[(server_ref, port)]),
            postconditions=[
                _exists(logical_id),
                ResourcePostcondition(kind="listening", target_ref=logical_id, detail=str(port)),
            ],
        )
        stream_ids[(server_ref, port)] = logical_id
    return stream_ids


def _attach_stream_dependencies(
    builder: _PlanBuilder,
    host_ids: list[str],
    stream_ids: dict[tuple[str, int], str],
) -> None:
    for host_id in host_ids:
        resource = builder.resources[host_id]
        payload = resource.payload
        if not isinstance(payload, HostPayload) or payload.protocol == "hysteria2":
            continue
        stream_id = stream_ids[(payload.address_server_ref, payload.public_port)]
        builder.resources[host_id] = make_resource(
            host_id,
            payload,
            dependencies=[*resource.dependencies, stream_id],
            postconditions=resource.postconditions,
        )


def _compile_relays(
    *,
    builder: _PlanBuilder,
    intent: SetupIntent,
    runtime_by_exit: dict[str, str],
    inbound_by_path: dict[tuple[str, str], str],
    stream_ids: dict[tuple[str, int], str],
) -> list[str]:
    exits = {exit_.id: exit_ for exit_ in intent.exits}
    host_ids: list[str] = []
    relay_listeners: set[tuple[str, int]] = set()
    for relay in sorted(intent.transparent_relays, key=lambda item: item.id):
        exit_ = exits[relay.exit_ref]
        path = next(path for path in exit_.paths if path.id == relay.protocol_path_ref)
        if path.protocol == "hysteria2":
            raise TopologyCompileError(f"Relay {relay.id} cannot forward UDP Hysteria2.")
        downstream_ref = exit_.server_ref
        downstream_port = path.public_port
        downstream_dependency = stream_ids[(exit_.server_ref, path.public_port)]
        hop_ids: dict[int, str] = {}
        for hop_index in reversed(range(len(relay.hop_server_refs))):
            server_ref = relay.hop_server_refs[hop_index]
            listener = (server_ref, relay.listen_port)
            if listener in relay_listeners:
                raise TopologyCompileError(
                    f"Relay listener {server_ref}:{relay.listen_port} is assigned more than once."
                )
            relay_listeners.add(listener)
            firewall_id = _add_firewall(
                builder,
                server_ref,
                "tcp",
                relay.listen_port,
                source_server_refs=(
                    [] if hop_index == 0 else [relay.hop_server_refs[hop_index - 1]]
                ),
            )
            hop_id = f"realm:{relay.id}:{hop_index}"
            builder.add(
                hop_id,
                RealmHopPayload(
                    chain_ref=relay.id,
                    hop_index=hop_index,
                    server_ref=server_ref,
                    listen_port=relay.listen_port,
                    target_server_ref=downstream_ref,
                    target_port=downstream_port,
                    advertised=hop_index == 0,
                ),
                dependencies=[downstream_dependency, firewall_id],
                postconditions=[
                    _exists(hop_id),
                    ResourcePostcondition(kind="listening", target_ref=hop_id, detail=str(relay.listen_port)),
                ],
            )
            hop_ids[hop_index] = hop_id
            downstream_ref = server_ref
            downstream_port = relay.listen_port
            downstream_dependency = hop_id

        first_hop_id = hop_ids[0]
        inbound_id = inbound_by_path[(exit_.id, path.id)]
        host_id = f"host:{relay.id}:{path.id}"
        host_ids.append(
            builder.add(
                host_id,
                HostPayload(
                    owner_ref=relay.id,
                    remark=f"Meridian v4 / {relay.id} / {path.id} / relay",
                    node_ref=runtime_by_exit[exit_.id],
                    inbound_ref=inbound_id,
                    address_server_ref=relay.hop_server_refs[0],
                    public_port=relay.listen_port,
                    protocol=path.protocol,
                    sni=relay.reality_sni or path.reality_sni or path.tls_sni,
                    host=path.host,
                    path=f"/{path.path}" if path.path else "",
                    fingerprint="chrome" if path.protocol == "reality" else "",
                    security_layer="TLS" if path.protocol in {"xhttp", "wss"} else "DEFAULT",
                ),
                dependencies=[first_hop_id, inbound_id],
            )
        )
    return host_ids


def _compile_routing(
    *,
    builder: _PlanBuilder,
    intent: SetupIntent,
    control_id: str,
    squad_id: str,
    runtime_by_exit: dict[str, str],
) -> list[str]:
    target_dependencies = dict(runtime_by_exit)
    for pool in sorted(intent.egress_pools, key=lambda item: item.id):
        pool_id = builder.add(
            f"egress-pool:{pool.id}",
            EgressPoolPayload(
                pool_id=pool.id,
                exit_refs=pool.exit_refs,
                strategy=pool.strategy,
                fail_closed=True,
            ),
            dependencies=[runtime_by_exit[exit_ref] for exit_ref in pool.exit_refs],
            postconditions=[
                _exists(f"egress-pool:{pool.id}"),
                ResourcePostcondition(kind="probe_succeeds", target_ref=f"egress-pool:{pool.id}", detail="fail closed"),
            ],
        )
        target_dependencies[pool.id] = pool_id

    gateway_ids: list[str] = []
    for gateway in sorted(intent.routing_gateways, key=lambda item: item.id):
        gateway_id = builder.add(
            f"gateway:{gateway.id}",
            RoutingGatewayPayload(
                gateway_id=gateway.id,
                server_ref=gateway.server_ref,
                bridge_path_ref=gateway.bridge_path_ref,
            ),
            dependencies=[control_id],
        )
        gateway_ids.append(gateway_id)

    gateway_by_name = {gateway.id: f"gateway:{gateway.id}" for gateway in intent.routing_gateways}
    service_users: set[tuple[str, str]] = set()
    for route in sorted(intent.routes, key=lambda item: (item.priority, item.id)):
        dependencies: list[str] = []
        if route.target_ref:
            dependencies.append(target_dependencies[route.target_ref])
        if route.source_gateway_ref:
            dependencies.append(gateway_by_name[route.source_gateway_ref])
            user_key = (route.source_gateway_ref, route.target_ref)
            if route.action == "route" and user_key not in service_users:
                service_users.add(user_key)
                builder.add(
                    f"service-user:{route.source_gateway_ref}:{route.target_ref}",
                    ServiceUserPayload(
                        username=f"meridian_{route.source_gateway_ref}_{route.target_ref}",
                        squad_ref=squad_id,
                        gateway_ref=route.source_gateway_ref,
                        target_ref=route.target_ref,
                    ),
                    dependencies=[squad_id, *dependencies],
                )
        builder.add(
            f"route:{route.id}",
            RouteRulePayload(
                route_id=route.id,
                priority=route.priority,
                match=route.match,
                match_values=route.match_values,
                action=route.action,
                target_ref=route.target_ref,
                source_gateway_ref=route.source_gateway_ref,
            ),
            dependencies=dependencies,
        )
    return gateway_ids


def _compile_probes(
    *,
    builder: _PlanBuilder,
    runtime_by_exit: dict[str, str],
    host_ids: list[str],
    user_ids: list[str],
    settings_id: str,
    gateway_ids: list[str],
) -> None:
    for exit_ref, runtime_id in sorted(runtime_by_exit.items()):
        builder.add(
            f"probe:node:{exit_ref}",
            ProbePayload(probe="node", target_ref=runtime_id, expected="connected"),
            dependencies=[runtime_id],
            postconditions=[ResourcePostcondition(kind="probe_succeeds", target_ref=runtime_id)],
        )
    for host_id in sorted(host_ids):
        host = builder.resources[host_id].payload
        assert isinstance(host, HostPayload)
        builder.add(
            f"probe:{host_id}",
            ProbePayload(
                probe="listener",
                target_ref=host_id,
                protocol=host.protocol,
                expected="connect",
            ),
            dependencies=[host_id],
            postconditions=[ResourcePostcondition(kind="probe_succeeds", target_ref=host_id)],
        )
    for user_id in sorted(user_ids):
        builder.add(
            f"probe:subscription:{user_id.removeprefix('user:')}",
            ProbePayload(probe="subscription", target_ref=user_id, expected="canonical Remnawave subscription"),
            dependencies=[user_id, settings_id, *host_ids],
            postconditions=[ResourcePostcondition(kind="probe_succeeds", target_ref=user_id)],
        )
    for gateway_id in sorted(gateway_ids):
        builder.add(
            f"probe:route:{gateway_id.removeprefix('gateway:')}",
            ProbePayload(probe="route", target_ref=gateway_id, expected="ordered first-match routing"),
            dependencies=[gateway_id],
            postconditions=[ResourcePostcondition(kind="probe_succeeds", target_ref=gateway_id)],
        )


def _reality_names_by_path(intent: SetupIntent) -> dict[tuple[str, str], list[str]]:
    names: dict[tuple[str, str], set[str]] = {}
    for exit_ in intent.exits:
        for path in exit_.paths:
            if path.protocol == "reality":
                names[(exit_.id, path.id)] = {path.reality_sni}
    for relay in intent.transparent_relays:
        if relay.reality_sni:
            names[(relay.exit_ref, relay.protocol_path_ref)].add(relay.reality_sni)
    result: dict[tuple[str, str], list[str]] = {}
    for key, accepted in names.items():
        exit_ = next(item for item in intent.exits if item.id == key[0])
        path = next(item for item in exit_.paths if item.id == key[1])
        result[key] = [path.reality_sni, *sorted(accepted - {path.reality_sni})]
    return result


def _ensure_certificate(
    builder: _PlanBuilder,
    *,
    exit_id: str,
    server_ref: str,
    hostname: str,
    runtime_id: str,
) -> str:
    logical_id = f"certificate:{exit_id}:{_token(hostname)}"
    if logical_id in builder.resources:
        return logical_id
    return builder.add(
        logical_id,
        CertificatePayload(server_ref=server_ref, hostname=hostname),
        dependencies=[runtime_id],
    )


def _add_firewall(
    builder: _PlanBuilder,
    server_ref: str,
    transport: Literal["tcp", "udp"],
    port: int,
    *,
    source_server_refs: list[str] | None = None,
) -> str:
    source_refs = sorted(set(source_server_refs or []))
    source_identity = ",".join(source_refs) or "public"
    logical_id = f"firewall:{_token(server_ref)}:{transport}:{port}:{_token(source_identity)}"
    if logical_id in builder.resources:
        return logical_id
    return builder.add(
        logical_id,
        FirewallRulePayload(
            server_ref=server_ref,
            transport=transport,
            port=port,
            source_server_refs=source_refs,
        ),
    )


def _reserve_stream_name(
    targets: dict[tuple[str, int, str], tuple[str, int]],
    *,
    server_ref: str,
    public_port: int,
    server_name: str,
    target: tuple[str, int],
) -> None:
    key = (server_ref, public_port, server_name)
    existing = targets.get(key)
    if existing is not None and existing != target:
        raise TopologyCompileError(
            f"{server_ref}:{public_port} cannot route SNI {server_name!r} to multiple workloads."
        )
    targets[key] = target


def _stream_id(server_ref: str, port: int) -> str:
    return f"nginx:{_token(server_ref)}:{port}:stream"


def _token(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "resource"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{normalized[:32]}-{digest}"


def _exists(target_ref: str) -> ResourcePostcondition:
    return ResourcePostcondition(kind="exists", target_ref=target_ref)


def _normalized_intent(intent: SetupIntent) -> dict[str, Any]:
    payload = intent.model_dump(mode="json")
    payload["exits"] = sorted(payload["exits"], key=lambda value: value["id"])
    for exit_ in payload["exits"]:
        exit_["paths"] = sorted(exit_["paths"], key=lambda value: value["id"])
    payload["transparent_relays"] = sorted(payload["transparent_relays"], key=lambda value: value["id"])
    payload["routing_gateways"] = sorted(payload["routing_gateways"], key=lambda value: value["id"])
    payload["egress_pools"] = sorted(payload["egress_pools"], key=lambda value: value["id"])
    payload["routes"] = sorted(payload["routes"], key=lambda value: (value["priority"], value["id"]))
    payload["access"]["users"] = sorted(payload["access"]["users"])
    payload["delivery"]["formats"] = sorted(payload["delivery"]["formats"])
    return payload
