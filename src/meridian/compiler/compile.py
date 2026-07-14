"""Pure finite compiler from setup intent to reviewed resources."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from meridian.compiler.allocations import PortAllocator
from meridian.compiler.builder import PlanBuilder
from meridian.compiler.client_delivery import compile_xray_delivery_hosts
from meridian.compiler.delivery import mihomo_template, xray_json_template
from meridian.compiler.errors import TopologyCompileError
from meridian.compiler.firewalls import add_firewall, stable_token
from meridian.compiler.gateway_compile import compile_routing_gateways
from meridian.compiler.models import (
    AccessUserPayload,
    CertificatePayload,
    ConfigProfilePayload,
    ControlPlaneRuntimePayload,
    ExternalSquadPayload,
    HostPayload,
    InboundPayload,
    InternalSquadPayload,
    NginxArtifactPayload,
    NginxRouteSpec,
    NodeBindingPayload,
    NodeRuntimePayload,
    ProbePayload,
    RealmHopPayload,
    ResourcePlan,
    ResourcePostcondition,
    SubscriptionSettingsPayload,
    SubscriptionTemplatePayload,
    canonical_hash,
    compute_plan_hash,
    make_resource,
)
from meridian.compiler.routing import bridge_inbound_id, compile_gateway_routing
from meridian.core.topology import ExitIntent, ProtocolPathIntent, SetupIntent


def compile_topology(intent: SetupIntent) -> ResourcePlan:
    """Compile complete setup intent without network, filesystem, or randomness."""
    builder = PlanBuilder()
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
    add_firewall(builder, intent.control.server_ref, "tcp", 80)
    add_firewall(builder, intent.control.server_ref, "tcp", 443)

    inbound_by_path: dict[tuple[str, str], str] = {}
    runtime_by_exit: dict[str, str] = {}
    allocated_path_ports: dict[tuple[str, str], int] = {}
    stream_routes: dict[tuple[str, int], list[NginxRouteSpec]] = defaultdict(list)
    stream_dependencies: dict[tuple[str, int], set[str]] = defaultdict(set)
    stream_targets: dict[tuple[str, int, str], tuple[str, int]] = {}
    stream_fallbacks: dict[tuple[str, int], str] = {}
    direct_host_ids: list[str] = []
    reality_names_by_path = _reality_names_by_path(intent)
    gateway_plans = {
        gateway.id: compile_gateway_routing(intent, gateway)
        for gateway in sorted(intent.routing_gateways, key=lambda item: item.id)
    }
    bridge_specs_by_exit: dict[str, list[tuple[str, InboundPayload, str]]] = defaultdict(list)
    bridge_ports: dict[tuple[str, str], int] = {}
    exits_by_id = {exit_.id: exit_ for exit_ in intent.exits}
    for plan in gateway_plans.values():
        for target_exit_ref in plan.target_exit_refs:
            target = exits_by_id[target_exit_ref]
            port = ports.auxiliary_tcp(
                target.server_ref,
                f"bridge:{plan.gateway.id}:{target_exit_ref}",
                base=40000,
                size=9999,
            )
            bridge_ports[(plan.gateway.id, target_exit_ref)] = port
            reality_path = next(path for path in target.paths if path.protocol == "reality")
            inbound_id = bridge_inbound_id(plan.gateway.id, target_exit_ref)
            firewall_id = add_firewall(
                builder,
                target.server_ref,
                "tcp",
                port,
                source_server_refs=[plan.gateway.server_ref],
            )
            bridge_specs_by_exit[target_exit_ref].append(
                (
                    inbound_id,
                    InboundPayload(
                        workload_ref=target_exit_ref,
                        protocol="reality",
                        purpose="bridge",
                        tag=f"meridian-{target_exit_ref}-bridge-{plan.gateway.id}",
                        listen_address="0.0.0.0",
                        listen_port=port,
                        public_port=port,
                        reality_sni=reality_path.reality_sni,
                        reality_server_names=[reality_path.reality_sni],
                    ),
                    firewall_id,
                )
            )

    exit_servers = [exit_.server_ref for exit_ in intent.exits]
    if len(exit_servers) != len(set(exit_servers)):
        raise TopologyCompileError(
            "Each V4 exit workload requires its own server because one Remnawave node runtime "
            "can activate only one config profile."
        )

    for exit_ in sorted(intent.exits, key=lambda item: item.id):
        profile_id = f"profile:{exit_.id}"
        sorted_paths = sorted(exit_.paths, key=lambda item: item.id)
        client_path_refs = [f"inbound:{exit_.id}:{path.id}" for path in sorted_paths]
        bridge_specs = sorted(bridge_specs_by_exit[exit_.id], key=lambda item: item[0])
        path_refs = [*client_path_refs, *(item[0] for item in bridge_specs)]
        inbound_specs: list[InboundPayload] = []
        for path in sorted_paths:
            if path.protocol == "hysteria2" and path.listen_port and path.listen_port != path.public_port:
                raise TopologyCompileError(f"Hysteria2 path {exit_.id}/{path.id} must listen on its public UDP port.")
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
        inbound_specs.extend(item[1] for item in bridge_specs)
        builder.add(
            profile_id,
            ConfigProfilePayload(
                workload_id=exit_.id,
                name=f"Meridian v4 / {exit_.id}",
                inbound_refs=path_refs,
                inbounds=inbound_specs,
                outbound_tags=["warp"] if exit_.warp else ["direct"],
            ),
            dependencies=[control_id, *(item[2] for item in bridge_specs)],
        )
        for path, inbound_spec in zip(
            sorted_paths,
            inbound_specs[: len(sorted_paths)],
            strict=True,
        ):
            inbound_id = f"inbound:{exit_.id}:{path.id}"
            builder.add(
                inbound_id,
                inbound_spec,
                dependencies=[profile_id],
            )
        for inbound_id, inbound_spec, firewall_id in bridge_specs:
            builder.add(
                inbound_id,
                inbound_spec,
                dependencies=[profile_id, firewall_id],
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
        add_firewall(
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
            stream_fallbacks=stream_fallbacks,
            direct_host_ids=direct_host_ids,
        )

    gateway_ids = compile_routing_gateways(
        builder=builder,
        ports=ports,
        intent=intent,
        control_id=control_id,
        plans=gateway_plans,
        bridge_ports=bridge_ports,
        runtime_by_exit=runtime_by_exit,
        inbound_by_path=inbound_by_path,
        allocated_path_ports=allocated_path_ports,
        stream_routes=stream_routes,
        stream_dependencies=stream_dependencies,
        stream_targets=stream_targets,
        stream_fallbacks=stream_fallbacks,
        direct_host_ids=direct_host_ids,
    )
    stream_ids = _compile_stream_artifacts(
        builder,
        stream_routes,
        stream_dependencies,
        stream_fallbacks,
    )
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
    template_ids: list[str] = []
    xray_template_id = ""
    if "xray_json" in intent.delivery.formats:
        xray_template_id = builder.add(
            f"template:{intent.delivery.template_name}:xray-json",
            SubscriptionTemplatePayload(
                name=f"Meridian v4 / {intent.delivery.template_name} / Xray JSON",
                profile_title=intent.delivery.profile_title,
                template_type="XRAY_JSON",
                template_json=xray_json_template(),
            ),
            dependencies=[control_id],
        )
        template_ids.append(xray_template_id)
    if "mihomo" in intent.delivery.formats:
        template_ids.append(
            builder.add(
                f"template:{intent.delivery.template_name}:mihomo",
                SubscriptionTemplatePayload(
                    name=f"Meridian v4 / {intent.delivery.template_name} / Mihomo",
                    profile_title=intent.delivery.profile_title,
                    template_type="MIHOMO",
                    template_yaml=mihomo_template(),
                ),
                dependencies=[control_id],
            )
        )
    if xray_template_id:
        compile_xray_delivery_hosts(
            builder,
            template_ref=xray_template_id,
        )
    external_squad_id = ""
    if template_ids:
        external_squad_id = builder.add(
            "external-squad:access",
            ExternalSquadPayload(
                name="Meridian v4 / delivery",
                template_refs=template_ids,
            ),
            dependencies=template_ids,
        )
    user_dependencies = [squad_id, *([external_squad_id] if external_squad_id else [])]
    user_ids = [
        builder.add(
            f"user:{username}",
            AccessUserPayload(
                username=username,
                squad_ref=squad_id,
                external_squad_ref=external_squad_id,
            ),
            dependencies=user_dependencies,
        )
        for username in sorted(intent.access.users)
    ]
    settings_id = builder.add(
        "subscription:settings",
        SubscriptionSettingsPayload(
            profile_title=intent.delivery.profile_title,
        ),
        dependencies=[control_id],
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
    builder: PlanBuilder,
    ports: PortAllocator,
    exit_: ExitIntent,
    runtime_id: str,
    inbound_by_path: dict[tuple[str, str], str],
    allocated_path_ports: dict[tuple[str, str], int],
    stream_routes: dict[tuple[str, int], list[NginxRouteSpec]],
    stream_dependencies: dict[tuple[str, int], set[str]],
    stream_targets: dict[tuple[str, int, str], tuple[str, int]],
    stream_fallbacks: dict[tuple[str, int], str],
    direct_host_ids: list[str],
) -> None:
    tls_groups: dict[tuple[str, int], list[ProtocolPathIntent]] = defaultdict(list)
    direct_dependencies: dict[str, list[str]] = defaultdict(list)
    for path in sorted(exit_.paths, key=lambda item: item.id):
        inbound_id = inbound_by_path[(exit_.id, path.id)]
        backend_port = allocated_path_ports[(exit_.id, path.id)]
        if path.protocol == "hysteria2":
            firewall_id = add_firewall(builder, exit_.server_ref, "udp", path.public_port)
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
            stream_fallbacks[stream_key] = path.reality_sni
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
            add_firewall(builder, exit_.server_ref, "tcp", path.public_port)
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
        http_id = f"nginx:{exit_.id}:{stable_token(tls_sni)}:http"
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
        add_firewall(builder, exit_.server_ref, "tcp", public_port)

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
    builder: PlanBuilder,
    routes: dict[tuple[str, int], list[NginxRouteSpec]],
    dependencies: dict[tuple[str, int], set[str]],
    fallbacks: dict[tuple[str, int], str],
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
                fallback_server_name=fallbacks.get((server_ref, port), ""),
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
    builder: PlanBuilder,
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
    builder: PlanBuilder,
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
            firewall_id = add_firewall(
                builder,
                server_ref,
                "tcp",
                relay.listen_port,
                source_server_refs=([] if hop_index == 0 else [relay.hop_server_refs[hop_index - 1]]),
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


def _compile_probes(
    *,
    builder: PlanBuilder,
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
    builder: PlanBuilder,
    *,
    exit_id: str,
    server_ref: str,
    hostname: str,
    runtime_id: str,
) -> str:
    logical_id = f"certificate:{exit_id}:{stable_token(hostname)}"
    if logical_id in builder.resources:
        return logical_id
    return builder.add(
        logical_id,
        CertificatePayload(server_ref=server_ref, hostname=hostname),
        dependencies=[runtime_id],
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
    return f"nginx:{stable_token(server_ref)}:{port}:stream"


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
