"""Finite resource plan produced by the V4 topology compiler."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any, Literal, Self, TypeAlias

from pydantic import Field, model_validator

from meridian.core.models import CoreModel
from meridian.core.topology import DeliveryFormat, EgressStrategy, ProtocolKind, RouteMatchKind, TrafficRouteAction

OwnershipMarker = Literal["meridian/v4"]
ResourceKind = Literal[
    "control_plane_runtime",
    "config_profile",
    "inbound",
    "node_binding",
    "node_runtime",
    "host",
    "internal_squad",
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
PostconditionKind = Literal[
    "exists",
    "matches_hash",
    "listening",
    "connected",
    "subscription_contains",
    "probe_succeeds",
]


class ControlPlaneRuntimePayload(CoreModel):
    kind: Literal["control_plane_runtime"] = "control_plane_runtime"
    server_ref: str
    public_hostname: str = ""


class InboundPayload(CoreModel):
    kind: Literal["inbound"] = "inbound"
    workload_ref: str
    protocol: ProtocolKind
    tag: str
    listen_port: int
    public_port: int
    reality_sni: str = ""
    reality_server_names: list[str] = Field(default_factory=list)
    tls_sni: str = ""
    host: str = ""
    path: str = ""

    @model_validator(mode="after")
    def validate_reality_names(self) -> InboundPayload:
        if self.protocol != "reality":
            if self.reality_server_names:
                raise ValueError("Only Reality Inbounds can declare accepted Reality server names.")
            return self
        if self.reality_server_names:
            expected = [
                self.reality_sni,
                *sorted(set(self.reality_server_names) - {self.reality_sni}),
            ]
            if self.reality_server_names != expected:
                raise ValueError("Reality server names must keep the primary SNI first, then sorted unique aliases.")
        return self


class ConfigProfilePayload(CoreModel):
    kind: Literal["config_profile"] = "config_profile"
    workload_id: str
    name: str
    inbound_refs: list[str]
    inbounds: list[InboundPayload]
    outbound_tags: list[str] = Field(default_factory=list)


class NodeBindingPayload(CoreModel):
    kind: Literal["node_binding"] = "node_binding"
    workload_ref: str
    server_ref: str
    name: str
    profile_ref: str
    inbound_refs: list[str]


class NodeRuntimePayload(CoreModel):
    kind: Literal["node_runtime"] = "node_runtime"
    workload_ref: str
    server_ref: str
    binding_ref: str
    api_port: int = 3010
    warp: bool = False


class HostPayload(CoreModel):
    kind: Literal["host"] = "host"
    owner_ref: str
    remark: str
    node_ref: str
    inbound_ref: str
    address_server_ref: str
    address: str = ""
    public_port: int
    protocol: ProtocolKind
    sni: str = ""
    host: str = ""
    path: str = ""
    alpn: str = ""
    fingerprint: str = ""
    security_layer: Literal["DEFAULT", "TLS", "NONE", "REALITY"] = "DEFAULT"
    advertised: bool = True


class InternalSquadPayload(CoreModel):
    kind: Literal["internal_squad"] = "internal_squad"
    name: str
    inbound_refs: list[str]


class AccessUserPayload(CoreModel):
    kind: Literal["access_user"] = "access_user"
    username: str
    squad_ref: str


class ServiceUserPayload(CoreModel):
    kind: Literal["service_user"] = "service_user"
    username: str
    squad_ref: str
    gateway_ref: str
    target_ref: str


class SubscriptionTemplatePayload(CoreModel):
    kind: Literal["subscription_template"] = "subscription_template"
    name: str
    profile_title: str
    formats: list[DeliveryFormat]


class SubscriptionSettingsPayload(CoreModel):
    kind: Literal["subscription_settings"] = "subscription_settings"
    template_ref: str
    preserve_unmanaged_response_rules: bool = True


class NginxRouteSpec(CoreModel):
    match: Literal["sni", "host_path"]
    server_names: list[str]
    path: str = ""
    backend_server_ref: str
    backend_port: int
    protocol: ProtocolKind | None = None


class NginxArtifactPayload(CoreModel):
    kind: Literal["nginx_artifact"] = "nginx_artifact"
    server_ref: str
    listener_port: int
    layer: Literal["stream", "http"]
    tls_hostname: str = ""
    routes: list[NginxRouteSpec]


class RealmHopPayload(CoreModel):
    kind: Literal["realm_hop"] = "realm_hop"
    chain_ref: str
    hop_index: int
    server_ref: str
    listen_port: int
    target_server_ref: str
    target_port: int
    advertised: bool


class FirewallRulePayload(CoreModel):
    kind: Literal["firewall_rule"] = "firewall_rule"
    server_ref: str
    transport: Literal["tcp", "udp"]
    port: int
    source_server_refs: list[str] = Field(default_factory=list)


class CertificatePayload(CoreModel):
    kind: Literal["certificate"] = "certificate"
    server_ref: str
    hostname: str


class RoutingGatewayPayload(CoreModel):
    kind: Literal["routing_gateway"] = "routing_gateway"
    gateway_id: str
    server_ref: str
    bridge_path_ref: str


class EgressPoolPayload(CoreModel):
    kind: Literal["egress_pool"] = "egress_pool"
    pool_id: str
    exit_refs: list[str]
    strategy: EgressStrategy
    fail_closed: Literal[True] = True


class RouteRulePayload(CoreModel):
    kind: Literal["route_rule"] = "route_rule"
    route_id: str
    priority: int
    match: RouteMatchKind
    match_values: list[str]
    action: TrafficRouteAction
    target_ref: str = ""
    source_gateway_ref: str = ""


class ProbePayload(CoreModel):
    kind: Literal["probe"] = "probe"
    probe: Literal["listener", "node", "subscription", "route", "fail_closed"]
    target_ref: str
    protocol: ProtocolKind | None = None
    expected: str = ""


ResourcePayload: TypeAlias = Annotated[
    ControlPlaneRuntimePayload
    | ConfigProfilePayload
    | InboundPayload
    | NodeBindingPayload
    | NodeRuntimePayload
    | HostPayload
    | InternalSquadPayload
    | AccessUserPayload
    | ServiceUserPayload
    | SubscriptionTemplatePayload
    | SubscriptionSettingsPayload
    | NginxArtifactPayload
    | RealmHopPayload
    | FirewallRulePayload
    | CertificatePayload
    | RoutingGatewayPayload
    | EgressPoolPayload
    | RouteRulePayload
    | ProbePayload,
    Field(discriminator="kind"),
]


class ResourcePostcondition(CoreModel):
    kind: PostconditionKind
    target_ref: str
    detail: str = ""


class CompiledResource(CoreModel):
    """One immutable reviewed resource with typed payload and dependencies."""

    logical_id: str
    ownership: OwnershipMarker = "meridian/v4"
    dependencies: list[str] = Field(default_factory=list)
    desired_hash: str
    payload: ResourcePayload
    postconditions: list[ResourcePostcondition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if not self.logical_id or re.fullmatch(r"[a-z0-9][a-z0-9:._-]*", self.logical_id) is None:
            raise ValueError("Compiled resource logical IDs must be lowercase stable identifiers.")
        if self.dependencies != sorted(set(self.dependencies)):
            raise ValueError(f"Dependencies for {self.logical_id} must be sorted and unique.")
        if self.logical_id in self.dependencies:
            raise ValueError(f"Resource {self.logical_id} cannot depend on itself.")
        if re.fullmatch(r"[0-9a-f]{64}", self.desired_hash) is None:
            raise ValueError(f"Resource {self.logical_id} has an invalid desired hash.")
        if self.desired_hash != compute_resource_hash(
            logical_id=self.logical_id,
            ownership=self.ownership,
            dependencies=self.dependencies,
            payload=self.payload,
            postconditions=self.postconditions,
        ):
            raise ValueError(f"Resource {self.logical_id} desired hash does not match its reviewed payload.")
        return self


class ResourcePlan(CoreModel):
    """Deterministic dependency-ordered plan produced without I/O."""

    schema_version: Literal["meridian.resource-plan/v1"] = Field(
        default="meridian.resource-plan/v1",
        alias="schema",
    )
    compiler_version: Literal["v4"] = "v4"
    intent_hash: str
    plan_hash: str
    resources: list[CompiledResource]

    @model_validator(mode="after")
    def validate_graph_and_hash(self) -> Self:
        ids = [resource.logical_id for resource in self.resources]
        if len(ids) != len(set(ids)):
            raise ValueError("Compiled resource logical IDs must be globally unique.")
        seen: set[str] = set()
        for resource in self.resources:
            missing = [dependency for dependency in resource.dependencies if dependency not in seen]
            if missing:
                raise ValueError(
                    f"Resource {resource.logical_id} appears before dependencies: {', '.join(missing)}."
                )
            seen.add(resource.logical_id)
        expected = compute_plan_hash(
            compiler_version=self.compiler_version,
            intent_hash=self.intent_hash,
            resources=self.resources,
        )
        if self.plan_hash != expected:
            raise ValueError("Resource plan hash does not match its immutable payloads.")
        return self


def canonical_hash(value: Any) -> str:
    """Return a stable SHA-256 hash for JSON-compatible data."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compute_resource_hash(
    *,
    logical_id: str,
    ownership: OwnershipMarker,
    dependencies: list[str],
    payload: ResourcePayload,
    postconditions: list[ResourcePostcondition],
) -> str:
    return canonical_hash(
        {
            "logical_id": logical_id,
            "ownership": ownership,
            "dependencies": dependencies,
            "payload": payload.model_dump(mode="json"),
            "postconditions": [condition.model_dump(mode="json") for condition in postconditions],
        }
    )


def compute_plan_hash(
    *,
    compiler_version: str,
    intent_hash: str,
    resources: list[CompiledResource],
) -> str:
    return canonical_hash(
        {
            "compiler_version": compiler_version,
            "intent_hash": intent_hash,
            "resources": [
                {"logical_id": resource.logical_id, "desired_hash": resource.desired_hash}
                for resource in resources
            ],
        }
    )


def make_resource(
    logical_id: str,
    payload: ResourcePayload,
    *,
    dependencies: list[str] | None = None,
    postconditions: list[ResourcePostcondition] | None = None,
) -> CompiledResource:
    """Construct a resource only after its full typed payload is known."""
    normalized_dependencies = sorted(set(dependencies or []))
    normalized_postconditions = postconditions or []
    desired_hash = compute_resource_hash(
        logical_id=logical_id,
        ownership="meridian/v4",
        dependencies=normalized_dependencies,
        payload=payload,
        postconditions=normalized_postconditions,
    )
    return CompiledResource(
        logical_id=logical_id,
        dependencies=normalized_dependencies,
        desired_hash=desired_hash,
        payload=payload,
        postconditions=normalized_postconditions,
    )
