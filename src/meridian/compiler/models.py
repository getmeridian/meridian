"""Finite resource plan produced by the V4 topology compiler."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any, Literal, Self, TypeAlias

from pydantic import Field, field_validator, model_validator

from meridian.compiler.names import (
    RemnawaveConfigProfileName,
    RemnawaveNodeName,
    RemnawaveSquadName,
    RemnawaveTemplateName,
    RemnawaveUsername,
)
from meridian.core.defaults import SUBSCRIPTION_UPDATE_INTERVAL_HOURS
from meridian.core.inputs import (
    IPAddressValue,
    OptionalCountryCodeValue,
    PortValue,
    ServerReferenceValue,
    SshUserValue,
    is_ip,
)
from meridian.core.models import CoreModel
from meridian.core.resources import ResourceKind as ResourceKind
from meridian.core.topology import (
    DEFAULT_TLS_FINGERPRINT,
    EgressOverrideMatch,
    EgressStrategy,
    ProtocolKind,
    RouteMatchKind,
    SubscriptionUpdateIntervalHours,
    TlsFingerprint,
    TrafficRouteAction,
)

COMPILER_VERSION: Literal["v4.1"] = "v4.1"
OwnershipMarker = Literal["meridian/v4"]
SubscriptionType = Literal["XRAY_JSON", "XRAY_BASE64", "MIHOMO", "STASH", "CLASH", "SINGBOX"]
# Every format except Xray JSON: hosts that exist only for Xray JSON injection hide from these.
NON_XRAY_JSON_TYPES: tuple[SubscriptionType, ...] = ("XRAY_BASE64", "MIHOMO", "STASH", "CLASH", "SINGBOX")
RuntimePinName = Annotated[
    str,
    Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9_.-]*$"),
]
RuntimePinValue = Annotated[
    str,
    Field(min_length=1, max_length=512, pattern=r"^\S+$"),
]
PostconditionKind = Literal[
    "exists",
    "matches_hash",
    "listening",
    "connected",
    "subscription_contains",
    "probe_succeeds",
]


class DeploymentTarget(CoreModel):
    """One resolved, secret-free SSH destination reviewed before apply."""

    host: IPAddressValue
    user: SshUserValue
    port: PortValue


class DeploymentContract(CoreModel):
    """Resolved deployment inputs that can change rendered or applied output."""

    server_targets: dict[ServerReferenceValue, DeploymentTarget] = Field(default_factory=dict)
    runtime_pins: dict[RuntimePinName, RuntimePinValue] = Field(default_factory=dict)
    renderer_contract: str = Field(default="", max_length=512, pattern=r"^\S*$")

    @field_validator("server_targets")
    @classmethod
    def sort_server_targets(cls, targets: dict[str, DeploymentTarget]) -> dict[str, DeploymentTarget]:
        return dict(sorted(targets.items()))

    @field_validator("runtime_pins")
    @classmethod
    def sort_runtime_pins(cls, pins: dict[str, str]) -> dict[str, str]:
        return dict(sorted(pins.items()))

    @model_validator(mode="after")
    def validate_complete_or_empty(self) -> Self:
        if not self.server_targets and not self.runtime_pins and not self.renderer_contract:
            return self
        missing = [
            name
            for name, present in (
                ("server targets", bool(self.server_targets)),
                ("runtime pins", bool(self.runtime_pins)),
                ("renderer contract", bool(self.renderer_contract)),
            )
            if not present
        ]
        if missing:
            raise ValueError("A deployment contract must be empty or include " + ", ".join(missing) + ".")
        return self


class ServerBaselinePayload(CoreModel):
    """Common host preparation required before any server-side mutation."""

    kind: Literal["server_baseline"] = "server_baseline"
    server_ref: str
    install_docker: bool = False
    harden: bool = True


class ControlPlaneRuntimePayload(CoreModel):
    kind: Literal["control_plane_runtime"] = "control_plane_runtime"
    server_ref: str
    public_hostname: str = ""
    internal_https_port: int = Field(default=8443, ge=1, le=65535)


class InboundPayload(CoreModel):
    kind: Literal["inbound"] = "inbound"
    workload_ref: str
    protocol: ProtocolKind
    purpose: Literal["client", "bridge", "relay"] = "client"
    tag: str
    listen_address: str = ""
    listen_port: int
    public_port: int
    reality_sni: str = ""
    reality_server_names: list[str] = Field(default_factory=list)
    tls_sni: str = ""
    host: str = ""
    path: str = ""
    # nginx in front passes the client's address (PROXY protocol or X-Forwarded-For).
    trust_forwarded_ip: bool = False
    # Self-steal: probes fall back to this loopback nginx TLS server instead of reality_sni:443.
    reality_dest_port: int = Field(default=0, ge=0, le=65535, exclude_if=lambda port: not port)
    # That server reads a PROXY header (the fleet forwards client IPs), so Reality sends one.
    reality_dest_proxy: bool = Field(default=False, exclude_if=lambda enabled: not enabled)

    @model_validator(mode="after")
    def validate_reality_names(self) -> InboundPayload:
        if self.protocol != "reality":
            if self.reality_server_names:
                raise ValueError("Only Reality Inbounds can declare accepted Reality server names.")
            if self.reality_dest_port or self.reality_dest_proxy:
                raise ValueError("Only Reality Inbounds have a camouflage fallback.")
            return self
        if self.reality_dest_proxy and not self.reality_dest_port:
            raise ValueError("A PROXY header needs a local self-steal fallback.")
        if self.reality_server_names:
            expected = [
                self.reality_sni,
                *sorted(set(self.reality_server_names) - {self.reality_sni}),
            ]
            if self.reality_server_names != expected:
                raise ValueError("Reality server names must keep the primary SNI first, then sorted unique aliases.")
        return self


class ServiceOutboundSpec(CoreModel):
    """One credential-resolved VLESS+Reality edge from a gateway to an exit."""

    edge_id: str
    tag: str
    service_user_ref: str
    target_workload_ref: str
    target_server_ref: str
    target_inbound_ref: str
    target_port: int
    target_sni: str
    target_fingerprint: TlsFingerprint = DEFAULT_TLS_FINGERPRINT


class EgressBalancerSpec(CoreModel):
    """One health-observed fail-closed Xray balancer."""

    pool_id: str
    tag: str
    outbound_tags: list[str] = Field(min_length=1)
    strategy: EgressStrategy
    probe_url: str
    fallback_tag: Literal["block"] = "block"


class WorkloadRouteSpec(CoreModel):
    """One first-match route embedded in a gateway Profile, or an exit's egress override."""

    route_id: str
    priority: int
    match: RouteMatchKind | EgressOverrideMatch
    match_values: list[str]
    target_type: Literal["outbound", "balancer"]
    target_tag: str


class ConfigProfilePayload(CoreModel):
    kind: Literal["config_profile"] = "config_profile"
    workload_id: str
    workload_kind: Literal["exit", "routing_gateway"] = "exit"
    name: RemnawaveConfigProfileName
    inbound_refs: list[str]
    inbounds: list[InboundPayload]
    outbound_tags: list[str] = Field(default_factory=list)
    geo_block: bool = False
    service_outbounds: list[ServiceOutboundSpec] = Field(default_factory=list)
    egress_balancers: list[EgressBalancerSpec] = Field(default_factory=list)
    routing_rules: list[WorkloadRouteSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_aggregate_profile(self) -> ConfigProfilePayload:
        if len(self.inbound_refs) != len(self.inbounds):
            raise ValueError("Profile Inbound references must align with its reviewed Inbound specs.")
        if self.routing_rules != sorted(
            self.routing_rules,
            key=lambda rule: (rule.priority, rule.route_id),
        ):
            raise ValueError("Profile routing rules must remain in reviewed priority order.")
        outbound_tags = {outbound.tag for outbound in self.service_outbounds}
        if len(outbound_tags) != len(self.service_outbounds):
            raise ValueError("Profile service Outbound tags must be unique.")
        for balancer in self.egress_balancers:
            if not set(balancer.outbound_tags) <= outbound_tags:
                raise ValueError(f"Balancer {balancer.pool_id} selects an unknown service Outbound.")
        return self


class NodeBindingPayload(CoreModel):
    kind: Literal["node_binding"] = "node_binding"
    workload_ref: str
    server_ref: str
    name: RemnawaveNodeName
    profile_ref: str
    inbound_refs: list[str]
    # The exit's region; empty leaves the panel's country (an operator choice) alone.
    country_code: OptionalCountryCodeValue = ""


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
    security_layer: Literal["DEFAULT", "TLS", "NONE"] = "DEFAULT"
    advertised: bool = True
    tags: list[str] = Field(default_factory=list)
    is_hidden: bool = False
    xray_json_template_ref: str = ""
    exclude_from_subscription_types: list[SubscriptionType] = Field(default_factory=list)
    view_position: int = 0


class InternalSquadPayload(CoreModel):
    kind: Literal["internal_squad"] = "internal_squad"
    name: RemnawaveSquadName
    inbound_refs: list[str]


class ExternalSquadPayload(CoreModel):
    kind: Literal["external_squad"] = "external_squad"
    name: RemnawaveSquadName
    template_refs: list[str]


class AccessUserPayload(CoreModel):
    kind: Literal["access_user"] = "access_user"
    username: RemnawaveUsername
    squad_ref: str
    external_squad_ref: str = ""


class ServiceUserPayload(CoreModel):
    kind: Literal["service_user"] = "service_user"
    username: RemnawaveUsername
    edge_id: str
    squad_ref: str
    gateway_ref: str
    target_ref: str


class SubscriptionTemplatePayload(CoreModel):
    kind: Literal["subscription_template"] = "subscription_template"
    name: RemnawaveTemplateName
    profile_title: str
    template_type: Literal["XRAY_JSON", "MIHOMO", "SINGBOX"]
    template_json: dict[str, Any] | None = None
    template_yaml: str = ""


# Hours between automatic subscription refreshes, sent as ``profile-update-interval``
# (Happ and the Clash family read whole hours). New exits and relays reach apps this fast.


class SubscriptionSettingsPayload(CoreModel):
    kind: Literal["subscription_settings"] = "subscription_settings"
    profile_title: str
    update_interval_hours: SubscriptionUpdateIntervalHours = SUBSCRIPTION_UPDATE_INTERVAL_HOURS
    randomize_hosts: bool = False
    # Apps Remnawave knows read Xray JSON (Happ, v2rayNG, Streisand, ...) get it instead of
    # base64 links, and with it the automatic failover between entries.
    serve_json_at_base_subscription: bool = True
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
    # "location" files join the control server's panel TLS server (the no-SNI route).
    layer: Literal["stream", "http", "location"]
    tls_hostname: str = ""
    fallback_server_name: str = ""
    routes: list[NginxRouteSpec]
    # Streams send the PROXY protocol header; TLS servers read it and set X-Forwarded-For.
    forward_client_ip: bool = False
    # Local listener that strips the header before the external camouflage fallback.
    fallback_strip_port: int = 0
    # Self-steal: unknown SNIs reach this loopback nginx TLS server instead of an outside site.
    fallback_port: int = Field(default=0, ge=0, le=65535, exclude_if=lambda port: not port)

    @model_validator(mode="after")
    def validate_fallback(self) -> NginxArtifactPayload:
        if self.fallback_port and (self.layer != "stream" or self.fallback_server_name):
            raise ValueError("A stream falls back to one camouflage: an outside site or a local server.")
        return self


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


# One acme.sh entry per IP address, shared with the panel: a second entry for the same IP
# would double Let's Encrypt's short-lived renewals and overwrite the panel's install target.
IP_CERTIFICATE_DIRECTORY = "/etc/ssl/meridian"


def certificate_directory(hostname: str) -> str:
    """Return the on-server directory holding the issued certificate for ``hostname``."""
    if is_ip(hostname):
        return IP_CERTIFICATE_DIRECTORY
    token = hashlib.sha256(hostname.encode("utf-8")).hexdigest()[:16]
    return f"/etc/ssl/meridian/certs/{token}"


class RoutingGatewayPayload(CoreModel):
    kind: Literal["routing_gateway"] = "routing_gateway"
    gateway_id: str
    server_ref: str
    bridge_path_ref: str
    profile_ref: str
    node_ref: str
    inbound_ref: str


class EgressPoolPayload(CoreModel):
    kind: Literal["egress_pool"] = "egress_pool"
    pool_id: str
    gateway_ref: str
    profile_ref: str
    exit_refs: list[str]
    outbound_tags: list[str]
    balancer_tag: str
    strategy: EgressStrategy
    probe_url: str
    fail_closed: Literal[True] = True


class RouteRulePayload(CoreModel):
    kind: Literal["route_rule"] = "route_rule"
    route_id: str
    gateway_ref: str
    profile_ref: str
    priority: int
    match: RouteMatchKind | EgressOverrideMatch
    match_values: list[str]
    action: TrafficRouteAction
    target_ref: str = ""
    source_gateway_ref: str = ""
    target_type: Literal["outbound", "balancer"]
    target_tag: str


class ProbePayload(CoreModel):
    kind: Literal["probe"] = "probe"
    probe: Literal["listener", "node", "subscription", "route", "fail_closed"]
    target_ref: str
    protocol: ProtocolKind | None = None
    expected: str = ""


# Meridian's own control service (#95): these kinds replace the Remnawave ones above when
# `ControlPlaneIntent.backend` is "meridian".


class ControlServicePayload(CoreModel):
    """`meridian serve` behind the control server's nginx, with its TLS and secret paths."""

    kind: Literal["control_service"] = "control_service"
    server_ref: str
    public_hostname: str = ""
    internal_https_port: int = Field(default=8443, ge=1, le=65535)
    service_port: int = Field(default=3000, ge=1, le=65535)


class AgentRuntimePayload(CoreModel):
    """The node agent on one server, enrolled with the control service over SSH."""

    kind: Literal["agent_runtime"] = "agent_runtime"
    workload_ref: str
    server_ref: str
    # Cloudflare WARP in SOCKS proxy mode beside the agent, installed over SSH like a panel node's.
    warp: bool = Field(default=False, exclude_if=lambda enabled: not enabled)


class NodeWorkloadPayload(CoreModel):
    """Everything one exit's agent runs: the reviewed Xray profile it renders into a node spec."""

    kind: Literal["node_workload"] = "node_workload"
    workload_ref: str
    server_ref: str
    agent_ref: str
    profile: ConfigProfilePayload


class ClientEndpointPayload(CoreModel):
    """One way into an exit that subscriptions publish."""

    kind: Literal["client_endpoint"] = "client_endpoint"
    owner_ref: str
    workload_ref: str
    remark: str
    protocol: ProtocolKind
    address_server_ref: str
    address: str = ""
    public_port: int
    sni: str = ""
    host: str = ""
    path: str = ""
    fingerprint: str = ""
    position: int = 0
    # Another fingerprint of an earlier entry: listed, but never in the same failover group.
    fingerprint_copy: bool = False


class ControlUserPayload(CoreModel):
    kind: Literal["control_user"] = "control_user"
    username: RemnawaveUsername


class ServiceSettingsPayload(CoreModel):
    kind: Literal["service_settings"] = "service_settings"
    profile_title: str
    update_interval_hours: SubscriptionUpdateIntervalHours = Field(
        default=SUBSCRIPTION_UPDATE_INTERVAL_HOURS,
        exclude_if=lambda value: value == SUBSCRIPTION_UPDATE_INTERVAL_HOURS,
    )


ResourcePayload: TypeAlias = Annotated[
    ServerBaselinePayload
    | ControlPlaneRuntimePayload
    | ConfigProfilePayload
    | InboundPayload
    | NodeBindingPayload
    | NodeRuntimePayload
    | HostPayload
    | InternalSquadPayload
    | ExternalSquadPayload
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
    | ProbePayload
    | ControlServicePayload
    | AgentRuntimePayload
    | NodeWorkloadPayload
    | ClientEndpointPayload
    | ControlUserPayload
    | ServiceSettingsPayload,
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
    compiler_version: Literal["v4.1"] = COMPILER_VERSION
    deployment_contract: DeploymentContract = Field(default_factory=DeploymentContract)
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
                raise ValueError(f"Resource {resource.logical_id} appears before dependencies: {', '.join(missing)}.")
            seen.add(resource.logical_id)
        expected = compute_plan_hash(
            compiler_version=self.compiler_version,
            deployment_contract=self.deployment_contract,
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
    deployment_contract: DeploymentContract | None = None,
    intent_hash: str,
    resources: list[CompiledResource],
) -> str:
    reviewed_contract = deployment_contract if deployment_contract is not None else DeploymentContract()
    return canonical_hash(
        {
            "compiler_version": compiler_version,
            "deployment_contract": reviewed_contract.model_dump(mode="json"),
            "intent_hash": intent_hash,
            "resources": [
                {"logical_id": resource.logical_id, "desired_hash": resource.desired_hash} for resource in resources
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
