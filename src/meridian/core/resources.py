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
]
