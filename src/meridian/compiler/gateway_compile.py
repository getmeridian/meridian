"""Compile executable Xray routing-gateway workloads."""

from __future__ import annotations

from meridian.compiler.allocations import PortAllocator
from meridian.compiler.builder import PlanBuilder
from meridian.compiler.errors import TopologyCompileError
from meridian.compiler.firewalls import add_firewall
from meridian.compiler.models import (
    ConfigProfilePayload,
    EgressPoolPayload,
    HostPayload,
    InboundPayload,
    InternalSquadPayload,
    NginxRouteSpec,
    NodeBindingPayload,
    NodeRuntimePayload,
    ResourcePostcondition,
    RouteRulePayload,
    RoutingGatewayPayload,
    ServiceUserPayload,
)
from meridian.compiler.names import internal_squad_name, node_name, profile_name, service_username
from meridian.compiler.routing import (
    GatewayRoutingPlan,
    bridge_inbound_id,
    bridge_squad_id,
    edge_id,
    service_user_id,
)
from meridian.core.topology import SetupIntent


def compile_routing_gateways(
    *,
    builder: PlanBuilder,
    ports: PortAllocator,
    intent: SetupIntent,
    control_id: str,
    plans: dict[str, GatewayRoutingPlan],
    bridge_ports: dict[tuple[str, str], int],
    runtime_by_exit: dict[str, str],
    inbound_by_path: dict[tuple[str, str], str],
    allocated_path_ports: dict[tuple[str, str], int],
    stream_routes: dict[tuple[str, int], list[NginxRouteSpec]],
    stream_dependencies: dict[tuple[str, int], set[str]],
    stream_targets: dict[tuple[str, int, str], tuple[str, int]],
    stream_fallbacks: dict[tuple[str, int], str],
    direct_host_ids: list[str],
) -> list[str]:
    gateway_ids: list[str] = []
    pools_by_id = {pool.id: pool for pool in intent.egress_pools}
    routes_by_id = {route.id: route for route in intent.routes}
    for gateway_ref, plan in sorted(plans.items()):
        service_user_refs: list[str] = []
        service_outbounds = []
        for outbound in plan.service_outbounds:
            target_exit_ref = outbound.target_workload_ref
            inbound_ref = bridge_inbound_id(gateway_ref, target_exit_ref)
            squad_ref = bridge_squad_id(gateway_ref, target_exit_ref)
            builder.add(
                squad_ref,
                InternalSquadPayload(
                    name=internal_squad_name(
                        f"bridge:{gateway_ref}:{target_exit_ref}",
                        f"Meridian v4 bridge {gateway_ref} {target_exit_ref}",
                    ),
                    inbound_refs=[inbound_ref],
                ),
                dependencies=[inbound_ref],
            )
            user_ref = service_user_id(gateway_ref, target_exit_ref)
            builder.add(
                user_ref,
                ServiceUserPayload(
                    username=service_username(edge_id(gateway_ref, target_exit_ref)),
                    edge_id=edge_id(gateway_ref, target_exit_ref),
                    squad_ref=squad_ref,
                    gateway_ref=gateway_ref,
                    target_ref=target_exit_ref,
                ),
                dependencies=[squad_ref],
            )
            service_user_refs.append(user_ref)
            service_outbounds.append(
                outbound.model_copy(update={"target_port": bridge_ports[(gateway_ref, target_exit_ref)]})
            )

        gateway = plan.gateway
        entry_path = plan.entry_path
        profile_id = f"profile:{gateway.id}"
        inbound_id = f"inbound:{gateway.id}:{entry_path.id}"
        listen_port = ports.auxiliary_tcp(
            gateway.server_ref,
            f"path:{gateway.id}:{entry_path.id}",
            base=10000,
            size=1999,
        )
        inbound = InboundPayload(
            workload_ref=gateway.id,
            protocol="reality",
            tag=f"meridian-{gateway.id}-reality",
            listen_port=listen_port,
            public_port=entry_path.public_port,
            reality_sni=entry_path.reality_sni,
            reality_server_names=[entry_path.reality_sni],
        )
        builder.add(
            profile_id,
            ConfigProfilePayload(
                workload_id=gateway.id,
                workload_kind="routing_gateway",
                name=profile_name(gateway.id),
                inbound_refs=[inbound_id],
                inbounds=[inbound],
                outbound_tags=["direct"],
                service_outbounds=service_outbounds,
                egress_balancers=list(plan.balancers),
                routing_rules=list(plan.rules),
            ),
            dependencies=[
                control_id,
                *service_user_refs,
                *(runtime_by_exit[exit_ref] for exit_ref in plan.target_exit_refs),
            ],
        )
        builder.add(inbound_id, inbound, dependencies=[profile_id])
        binding_id = builder.add(
            f"binding:{gateway.id}",
            NodeBindingPayload(
                workload_ref=gateway.id,
                server_ref=gateway.server_ref,
                name=node_name(gateway.id),
                profile_ref=profile_id,
                inbound_refs=[inbound_id],
            ),
            dependencies=[profile_id, inbound_id],
        )
        runtime_id = builder.add(
            f"node:{gateway.id}",
            NodeRuntimePayload(
                workload_ref=gateway.id,
                server_ref=gateway.server_ref,
                binding_ref=binding_id,
            ),
            dependencies=[binding_id],
            postconditions=[
                _exists(f"node:{gateway.id}"),
                ResourcePostcondition(kind="connected", target_ref=f"node:{gateway.id}"),
            ],
        )
        add_firewall(
            builder,
            gateway.server_ref,
            "tcp",
            NodeRuntimePayload(
                workload_ref=gateway.id,
                server_ref=gateway.server_ref,
                binding_ref=binding_id,
            ).api_port,
            source_server_refs=[intent.control.server_ref],
        )
        inbound_by_path[(gateway.id, entry_path.id)] = inbound_id
        allocated_path_ports[(gateway.id, entry_path.id)] = listen_port
        _compile_reality_endpoint(
            builder=builder,
            gateway_ref=gateway.id,
            server_ref=gateway.server_ref,
            public_port=entry_path.public_port,
            reality_sni=entry_path.reality_sni,
            listen_port=listen_port,
            runtime_id=runtime_id,
            inbound_id=inbound_id,
            stream_routes=stream_routes,
            stream_dependencies=stream_dependencies,
            stream_targets=stream_targets,
            stream_fallbacks=stream_fallbacks,
            direct_host_ids=direct_host_ids,
        )

        pool_resource_ids: list[str] = []
        for balancer in plan.balancers:
            pool = pools_by_id[balancer.pool_id]
            logical_id = f"egress-pool:{gateway.id}:{pool.id}"
            pool_resource_ids.append(
                builder.add(
                    logical_id,
                    EgressPoolPayload(
                        pool_id=pool.id,
                        gateway_ref=gateway.id,
                        profile_ref=profile_id,
                        exit_refs=pool.exit_refs,
                        outbound_tags=balancer.outbound_tags,
                        balancer_tag=balancer.tag,
                        strategy=pool.strategy,
                        probe_url=str(pool.probe_url),
                        fail_closed=True,
                    ),
                    dependencies=[profile_id],
                )
            )
        route_resource_ids: list[str] = []
        for rule in plan.rules:
            source = routes_by_id.get(rule.route_id)
            logical_id = f"route:{gateway.id}:{rule.route_id}"
            route_resource_ids.append(
                builder.add(
                    logical_id,
                    RouteRulePayload(
                        route_id=rule.route_id,
                        gateway_ref=gateway.id,
                        profile_ref=profile_id,
                        priority=rule.priority,
                        match=rule.match,
                        match_values=rule.match_values,
                        action=source.action if source is not None else "route",
                        target_ref=(source.target_ref if source is not None else intent.default_egress_ref),
                        source_gateway_ref=gateway.id,
                        target_type=rule.target_type,
                        target_tag=rule.target_tag,
                    ),
                    dependencies=[profile_id],
                )
            )
        gateway_id = builder.add(
            f"gateway:{gateway.id}",
            RoutingGatewayPayload(
                gateway_id=gateway.id,
                server_ref=gateway.server_ref,
                bridge_path_ref=gateway.bridge_path_ref,
                profile_ref=profile_id,
                node_ref=runtime_id,
                inbound_ref=inbound_id,
            ),
            dependencies=[runtime_id, *pool_resource_ids, *route_resource_ids],
        )
        gateway_ids.append(gateway_id)
    return gateway_ids


def _compile_reality_endpoint(
    *,
    builder: PlanBuilder,
    gateway_ref: str,
    server_ref: str,
    public_port: int,
    reality_sni: str,
    listen_port: int,
    runtime_id: str,
    inbound_id: str,
    stream_routes: dict[tuple[str, int], list[NginxRouteSpec]],
    stream_dependencies: dict[tuple[str, int], set[str]],
    stream_targets: dict[tuple[str, int, str], tuple[str, int]],
    stream_fallbacks: dict[tuple[str, int], str],
    direct_host_ids: list[str],
) -> None:
    stream_key = (server_ref, public_port)
    target = (server_ref, listen_port)
    route_key = (server_ref, public_port, reality_sni)
    existing = stream_targets.get(route_key)
    if existing is not None and existing != target:
        raise TopologyCompileError(
            f"{server_ref}:{public_port} cannot route SNI {reality_sni!r} to multiple workloads."
        )
    stream_targets[route_key] = target
    stream_fallbacks[stream_key] = reality_sni
    stream_routes[stream_key].append(
        NginxRouteSpec(
            match="sni",
            server_names=[reality_sni],
            backend_server_ref=server_ref,
            backend_port=listen_port,
            protocol="reality",
        )
    )
    stream_dependencies[stream_key].update({runtime_id, inbound_id})
    add_firewall(builder, server_ref, "tcp", public_port)
    host_id = f"host:{gateway_ref}:entry:direct"
    direct_host_ids.append(
        builder.add(
            host_id,
            HostPayload(
                owner_ref=gateway_ref,
                remark=f"Meridian v4 / {gateway_ref} / entry / smart",
                node_ref=runtime_id,
                inbound_ref=inbound_id,
                address_server_ref=server_ref,
                public_port=public_port,
                protocol="reality",
                sni=reality_sni,
                fingerprint="chrome",
            ),
            dependencies=[runtime_id, inbound_id],
        )
    )


def _exists(target_ref: str) -> ResourcePostcondition:
    return ResourcePostcondition(kind="exists", target_ref=target_ref)
