"""Topology and setup entries for the public schema catalog."""

from __future__ import annotations

from pydantic import BaseModel

from meridian.core.setup import (
    SetupAccessSelection,
    SetupDraft,
    SetupExitPaths,
    SetupExitRole,
    SetupGatewayRole,
    SetupPathSelection,
    SetupRelayRole,
    SetupRoleSelection,
    SetupRoutingSelection,
)
from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    DeliveryIntent,
    EgressPoolIntent,
    ExitIntent,
    OrderedRouteIntent,
    ProtocolPathIntent,
    RegionalTrafficDecision,
    RouteCard,
    RoutingGatewayIntent,
    RoutingPolicyDraft,
    SetupIntent,
    TopologyBuilderDraft,
    TopologyServerCapabilities,
    TopologyServerShelfItem,
    TrafficRouteRule,
    TransparentRelayIntent,
)

TOPOLOGY_SCHEMAS: dict[str, type[BaseModel]] = {
    "setup-access-selection": SetupAccessSelection,
    "setup-draft": SetupDraft,
    "setup-exit-paths": SetupExitPaths,
    "setup-exit-role": SetupExitRole,
    "setup-gateway-role": SetupGatewayRole,
    "setup-path-selection": SetupPathSelection,
    "setup-relay-role": SetupRelayRole,
    "setup-role-selection": SetupRoleSelection,
    "setup-routing-selection": SetupRoutingSelection,
    "setup-intent": SetupIntent,
    "access-intent": AccessIntent,
    "control-plane-intent": ControlPlaneIntent,
    "delivery-intent": DeliveryIntent,
    "egress-pool-intent": EgressPoolIntent,
    "exit-intent": ExitIntent,
    "ordered-route-intent": OrderedRouteIntent,
    "protocol-path-intent": ProtocolPathIntent,
    "routing-gateway-intent": RoutingGatewayIntent,
    "transparent-relay-intent": TransparentRelayIntent,
    "routing-policy-draft": RoutingPolicyDraft,
    "regional-traffic-decision": RegionalTrafficDecision,
    "route-card": RouteCard,
    "topology-builder-draft": TopologyBuilderDraft,
    "topology-server-shelf-item": TopologyServerShelfItem,
    "topology-server-capabilities": TopologyServerCapabilities,
    "traffic-route-rule": TrafficRouteRule,
}
