"""Pure expansion of V4 gateway intent into Xray routing fragments."""

from __future__ import annotations

from dataclasses import dataclass

from meridian.compiler.models import (
    EgressBalancerSpec,
    ServiceOutboundSpec,
    WorkloadRouteSpec,
)
from meridian.core.topology import (
    EgressPoolIntent,
    ProtocolPathIntent,
    RouteMatchKind,
    RoutingGatewayIntent,
    SetupIntent,
    TrafficRouteAction,
)


@dataclass(frozen=True)
class GatewayRoutingPlan:
    gateway: RoutingGatewayIntent
    entry_path: ProtocolPathIntent
    target_exit_refs: tuple[str, ...]
    service_outbounds: tuple[ServiceOutboundSpec, ...]
    balancers: tuple[EgressBalancerSpec, ...]
    rules: tuple[WorkloadRouteSpec, ...]


def compile_gateway_routing(intent: SetupIntent, gateway: RoutingGatewayIntent) -> GatewayRoutingPlan:
    """Expand one gateway without allocating ports or remote credentials."""
    entry_path = _entry_path(intent, gateway)
    pools = {pool.id: pool for pool in intent.egress_pools}
    relevant_routes = sorted(
        (route for route in intent.routes if route.enabled and route.source_gateway_ref in {"", gateway.id}),
        key=lambda route: (route.priority, route.id),
    )
    target_refs = {
        intent.default_egress_ref,
        *(route.target_ref for route in relevant_routes if route.action == "route"),
    }
    target_exits = sorted({exit_ref for target_ref in target_refs for exit_ref in _expand_target(target_ref, pools)})
    service_outbounds = tuple(
        ServiceOutboundSpec(
            edge_id=edge_id(gateway.id, exit_ref),
            tag=edge_outbound_tag(gateway.id, exit_ref),
            service_user_ref=service_user_id(gateway.id, exit_ref),
            target_workload_ref=exit_ref,
            target_server_ref=_exit_server_ref(intent, exit_ref),
            target_inbound_ref=bridge_inbound_id(gateway.id, exit_ref),
            target_port=0,
            target_sni=_exit_reality_path(intent, exit_ref).reality_sni,
        )
        for exit_ref in target_exits
    )
    used_pool_ids = sorted(target_ref for target_ref in target_refs if target_ref in pools)
    balancers = tuple(_balancer(gateway.id, pools[pool_id]) for pool_id in used_pool_ids)
    rules = [
        _route_spec(
            gateway.id,
            route.id,
            route.priority,
            route.match,
            route.match_values,
            route.action,
            route.target_ref,
            pools,
        )
        for route in relevant_routes
    ]
    if not any(rule.match == "all" for rule in relevant_routes):
        rules.append(
            _route_spec(
                gateway.id,
                "default",
                max((route.priority for route in relevant_routes), default=-1) + 1,
                "all",
                [],
                "route",
                intent.default_egress_ref,
                pools,
            )
        )
    return GatewayRoutingPlan(
        gateway=gateway,
        entry_path=entry_path,
        target_exit_refs=tuple(target_exits),
        service_outbounds=service_outbounds,
        balancers=balancers,
        rules=tuple(rules),
    )


def edge_id(gateway_ref: str, exit_ref: str) -> str:
    return f"{gateway_ref}:{exit_ref}"


def bridge_inbound_id(gateway_ref: str, exit_ref: str) -> str:
    return f"inbound:{exit_ref}:bridge:{gateway_ref}"


def bridge_squad_id(gateway_ref: str, exit_ref: str) -> str:
    return f"squad:bridge:{gateway_ref}:{exit_ref}"


def service_user_id(gateway_ref: str, exit_ref: str) -> str:
    return f"service-user:{gateway_ref}:{exit_ref}"


def edge_outbound_tag(gateway_ref: str, exit_ref: str) -> str:
    return f"meridian-edge-{gateway_ref}-{exit_ref}"


def pool_balancer_tag(gateway_ref: str, pool_ref: str) -> str:
    return f"meridian-pool-{gateway_ref}-{pool_ref}"


def _entry_path(intent: SetupIntent, gateway: RoutingGatewayIntent) -> ProtocolPathIntent:
    return next(path for exit_ in intent.exits for path in exit_.paths if path.id == gateway.bridge_path_ref)


def _expand_target(
    target_ref: str,
    pools: dict[str, EgressPoolIntent],
) -> list[str]:
    pool = pools.get(target_ref)
    return pool.exit_refs if pool is not None else [target_ref]


def _exit_server_ref(intent: SetupIntent, exit_ref: str) -> str:
    return next(exit_.server_ref for exit_ in intent.exits if exit_.id == exit_ref)


def _exit_reality_path(intent: SetupIntent, exit_ref: str) -> ProtocolPathIntent:
    exit_ = next(exit_ for exit_ in intent.exits if exit_.id == exit_ref)
    return next(path for path in exit_.paths if path.protocol == "reality")


def _balancer(gateway_ref: str, pool: EgressPoolIntent) -> EgressBalancerSpec:
    return EgressBalancerSpec(
        pool_id=pool.id,
        tag=pool_balancer_tag(gateway_ref, pool.id),
        outbound_tags=[edge_outbound_tag(gateway_ref, exit_ref) for exit_ref in pool.exit_refs],
        strategy=pool.strategy,
        probe_url=str(pool.probe_url),
    )


def _route_spec(
    gateway_ref: str,
    route_id: str,
    priority: int,
    match: RouteMatchKind,
    match_values: list[str],
    action: TrafficRouteAction,
    target_ref: str,
    pools: dict[str, EgressPoolIntent],
) -> WorkloadRouteSpec:
    if action == "block":
        return WorkloadRouteSpec(
            route_id=route_id,
            priority=priority,
            match=match,
            match_values=match_values,
            target_type="outbound",
            target_tag="block",
        )
    if target_ref in pools:
        return WorkloadRouteSpec(
            route_id=route_id,
            priority=priority,
            match=match,
            match_values=match_values,
            target_type="balancer",
            target_tag=pool_balancer_tag(gateway_ref, target_ref),
        )
    return WorkloadRouteSpec(
        route_id=route_id,
        priority=priority,
        match=match,
        match_values=match_values,
        target_type="outbound",
        target_tag=edge_outbound_tag(gateway_ref, target_ref),
    )
