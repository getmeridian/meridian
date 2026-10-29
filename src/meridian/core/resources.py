"""Shared resource-kind contract for compiler and public result models."""

from typing import Literal

ResourceKind = Literal[
    "server_baseline",
    "control_plane_runtime",
    "config_profile",
    "inbound",
    "node_binding",
    "node_runtime",
    "host",
    "internal_squad",
    "external_squad",
    "access_user",
    "service_user",
    "subscription_template",
    "subscription_settings",
    "nginx_artifact",
    "realm_hop",
    "firewall_rule",
    "certificate",
    "routing_gateway",
    "egress_pool",
    "route_rule",
    "probe",
    "control_service",
    "agent_runtime",
    "node_workload",
    "client_endpoint",
    "control_user",
    "service_settings",
]

# The kinds a reviewed plan applies. The compiler's panel-shaped first pass names
# the rest, and `compiler.meridian_backend` maps each onto one of these.
APPLIED_KINDS: frozenset[ResourceKind] = frozenset(
    {
        "server_baseline",
        "nginx_artifact",
        "realm_hop",
        "firewall_rule",
        "certificate",
        "control_service",
        "agent_runtime",
        "node_workload",
        "client_endpoint",
        "control_user",
        "service_settings",
    }
)
