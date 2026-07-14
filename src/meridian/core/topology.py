"""Topology capability and routing-policy contracts."""

from __future__ import annotations

import ipaddress
from typing import Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from meridian.core.inputs import (
    CountryCodeValue,
    OptionalCountryCodeValue,
    OptionalHostnameValue,
    OptionalServerReferenceValue,
    OptionalTransportPathValue,
    PortValue,
    RequiredNameValue,
    ServerReferenceValue,
    ServerTitleValue,
    validate_country_code_value,
    validate_hostname_value,
)
from meridian.core.models import CoreModel

ServerCapability = Literal["panel", "exit", "relay", "routing_gateway"]
TrafficScope = Literal["default", "country"]
TrafficRouteAction = Literal["route", "block"]
RegionalTrafficMode = Literal["block", "regional_exit", "default_exit"]
ProtocolKind = Literal["reality", "xhttp", "wss", "hysteria2"]
RouteMatchKind = Literal["all", "country", "domain", "ip", "network"]
EgressStrategy = Literal["priority", "least_ping"]
DeliveryFormat = Literal["base64", "xray_json", "mihomo"]


def _default_delivery_formats() -> list[DeliveryFormat]:
    return ["base64", "xray_json", "mihomo"]


class ControlPlaneIntent(CoreModel):
    """One server that owns the Remnawave control-plane workload."""

    server_ref: ServerReferenceValue
    public_hostname: OptionalHostnameValue = ""
    title: ServerTitleValue = "Meridian"


class ProtocolPathIntent(CoreModel):
    """One protocol-specific public path backed by an exit workload."""

    id: RequiredNameValue
    protocol: ProtocolKind
    listen_port: int = Field(default=0, ge=0, le=65535)
    public_port: PortValue = 443
    reality_sni: OptionalHostnameValue = ""
    tls_sni: OptionalHostnameValue = ""
    host: OptionalHostnameValue = ""
    path: OptionalTransportPathValue = ""

    @model_validator(mode="after")
    def validate_protocol_fields(self) -> Self:
        if self.protocol == "reality":
            if not self.reality_sni:
                raise ValueError("Reality paths require a camouflage SNI.")
            if self.tls_sni or self.host or self.path:
                raise ValueError("Reality camouflage SNI is separate from TLS Host and path fields.")
            return self
        if self.reality_sni:
            raise ValueError(f"{self.protocol} paths cannot use a Reality camouflage SNI.")
        if self.protocol in {"xhttp", "wss"}:
            if not self.tls_sni or not self.host or not self.path:
                raise ValueError(f"{self.protocol.upper()} paths require certificate SNI, Host, and path.")
            return self
        if not self.tls_sni:
            raise ValueError("Hysteria2 paths require a certificate SNI.")
        if self.host or self.path:
            raise ValueError("Hysteria2 paths do not use HTTP Host or path fields.")
        return self


class ExitIntent(CoreModel):
    """One independent Xray exit workload and its public protocol paths."""

    id: RequiredNameValue
    server_ref: ServerReferenceValue
    region: OptionalCountryCodeValue = ""
    paths: list[ProtocolPathIntent] = Field(min_length=1)
    warp: bool = False

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        path_ids = [path.id for path in self.paths]
        if len(path_ids) != len(set(path_ids)):
            raise ValueError(f"Exit {self.id} lists a protocol path ID more than once.")
        protocols = [path.protocol for path in self.paths]
        if len(protocols) != len(set(protocols)):
            raise ValueError(f"Exit {self.id} lists a protocol more than once.")
        if "reality" not in protocols:
            raise ValueError(f"Exit {self.id} must keep a Reality path.")
        return self


class TransparentRelayIntent(CoreModel):
    """A client-first Realm hop list whose first server is the advertised endpoint."""

    id: RequiredNameValue
    hop_server_refs: list[ServerReferenceValue] = Field(min_length=1)
    exit_ref: RequiredNameValue
    protocol_path_ref: RequiredNameValue
    listen_port: PortValue = 443
    reality_sni: OptionalHostnameValue = ""

    @field_validator("hop_server_refs")
    @classmethod
    def reject_duplicate_hops(cls, hops: list[str]) -> list[str]:
        if len(hops) != len(set(hops)):
            raise ValueError("A transparent relay chain cannot visit the same server twice.")
        return hops


class RoutingGatewayIntent(CoreModel):
    """An Xray gateway that accepts service-user traffic for server-side routing."""

    id: RequiredNameValue
    server_ref: ServerReferenceValue
    bridge_path_ref: RequiredNameValue


class EgressPoolIntent(CoreModel):
    """An ordered set of exits used for server-side failover."""

    id: RequiredNameValue
    exit_refs: list[RequiredNameValue] = Field(min_length=1)
    strategy: EgressStrategy = "priority"
    fail_closed: bool = True

    @model_validator(mode="after")
    def validate_pool(self) -> Self:
        if len(self.exit_refs) != len(set(self.exit_refs)):
            raise ValueError(f"Egress pool {self.id} lists an exit more than once.")
        if not self.fail_closed:
            raise ValueError("Meridian egress pools must fail closed when every exit is down.")
        return self


class OrderedRouteIntent(CoreModel):
    """One explicit first-match routing rule."""

    id: RequiredNameValue
    priority: int = Field(ge=0)
    match: RouteMatchKind = "all"
    match_values: list[str] = Field(default_factory=list)
    action: TrafficRouteAction = "route"
    target_ref: OptionalServerReferenceValue = ""
    source_gateway_ref: OptionalServerReferenceValue = ""
    enabled: bool = True

    @field_validator("match_values")
    @classmethod
    def validate_match_values(cls, values: list[str], info: ValidationInfo) -> list[str]:
        kind = info.data.get("match", "all")
        if kind == "all":
            if values:
                raise ValueError("Catch-all routes cannot include match values.")
            return values
        if not values:
            raise ValueError(f"{kind} routes require at least one match value.")
        normalized: list[str] = []
        for value in values:
            if kind == "country":
                normalized.append(validate_country_code_value(value))
            elif kind == "domain":
                normalized.append(validate_hostname_value(value))
            elif kind == "ip":
                try:
                    normalized.append(str(ipaddress.ip_network(value, strict=False)))
                except ValueError as exc:
                    raise ValueError(f"Enter a valid IP network instead of {value!r}.") from exc
            elif kind == "network":
                network = value.strip().lower()
                if network not in {"tcp", "udp"}:
                    raise ValueError("Network route values must be tcp or udp.")
                normalized.append(network)
        if len(normalized) != len(set(normalized)):
            raise ValueError(f"{kind} route values must be unique.")
        return normalized

    @model_validator(mode="after")
    def validate_action(self) -> Self:
        if self.action == "route" and not self.target_ref:
            raise ValueError("Routing rules require an exit or egress-pool target.")
        if self.action == "block" and self.target_ref:
            raise ValueError("Blocked routes cannot have an egress target.")
        return self


class AccessIntent(CoreModel):
    """Meridian-owned access squad and human users."""

    squad_name: ServerTitleValue = "Meridian Access"
    users: list[RequiredNameValue] = Field(min_length=1)

    @field_validator("users")
    @classmethod
    def reject_duplicate_users(cls, users: list[str]) -> list[str]:
        if len(users) != len(set(users)):
            raise ValueError("Access usernames must be unique.")
        return users


class DeliveryIntent(CoreModel):
    """Canonical Remnawave subscription presentation settings."""

    profile_title: ServerTitleValue = "Meridian"
    template_name: RequiredNameValue = "meridian"
    formats: list[DeliveryFormat] = Field(default_factory=_default_delivery_formats, min_length=1)

    @field_validator("formats")
    @classmethod
    def reject_duplicate_formats(cls, formats: list[DeliveryFormat]) -> list[DeliveryFormat]:
        if len(formats) != len(set(formats)):
            raise ValueError("List each delivery format once.")
        return formats


class SetupIntent(CoreModel):
    """Complete user intent consumed by the pure topology compiler."""

    control: ControlPlaneIntent
    exits: list[ExitIntent] = Field(min_length=1)
    transparent_relays: list[TransparentRelayIntent] = Field(default_factory=list)
    routing_gateways: list[RoutingGatewayIntent] = Field(default_factory=list)
    egress_pools: list[EgressPoolIntent] = Field(default_factory=list)
    routes: list[OrderedRouteIntent] = Field(default_factory=list)
    default_egress_ref: RequiredNameValue
    access: AccessIntent
    delivery: DeliveryIntent = Field(default_factory=DeliveryIntent)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        resources = [
            *[exit_.id for exit_ in self.exits],
            *[relay.id for relay in self.transparent_relays],
            *[gateway.id for gateway in self.routing_gateways],
            *[pool.id for pool in self.egress_pools],
            *[route.id for route in self.routes],
        ]
        if len(resources) != len(set(resources)):
            raise ValueError("Topology resource IDs must be globally unique.")

        exits = {exit_.id: exit_ for exit_ in self.exits}
        pools = {pool.id: pool for pool in self.egress_pools}
        gateways = {gateway.id for gateway in self.routing_gateways}
        valid_targets = set(exits) | set(pools)
        if self.default_egress_ref not in valid_targets:
            raise ValueError(f"Default egress {self.default_egress_ref!r} does not name an exit or pool.")

        for pool in self.egress_pools:
            missing = [exit_ref for exit_ref in pool.exit_refs if exit_ref not in exits]
            if missing:
                raise ValueError(f"Egress pool {pool.id} references unknown exits: {', '.join(missing)}.")

        for relay in self.transparent_relays:
            exit_ = exits.get(relay.exit_ref)
            if exit_ is None:
                raise ValueError(f"Relay {relay.id} references unknown exit {relay.exit_ref!r}.")
            path = next((candidate for candidate in exit_.paths if candidate.id == relay.protocol_path_ref), None)
            if path is None:
                raise ValueError(
                    f"Relay {relay.id} references unknown path {relay.protocol_path_ref!r} on exit {relay.exit_ref}."
                )
            if path.protocol == "hysteria2":
                raise ValueError("Transparent Realm chains cannot relay Hysteria2 or other UDP paths.")
            if relay.reality_sni and path.protocol != "reality":
                raise ValueError("A custom relay Reality SNI can only target a Reality path.")
            if exit_.server_ref in relay.hop_server_refs:
                raise ValueError(f"Relay {relay.id} cannot include its exit server as a Realm hop.")

        priorities: set[int] = set()
        for route in self.routes:
            if route.priority in priorities:
                raise ValueError(f"Route priority {route.priority} is used more than once.")
            priorities.add(route.priority)
            if route.action == "route" and route.target_ref not in valid_targets:
                raise ValueError(f"Route {route.id} references unknown egress target {route.target_ref!r}.")
            if route.source_gateway_ref and route.source_gateway_ref not in gateways:
                raise ValueError(f"Route {route.id} references unknown routing gateway {route.source_gateway_ref!r}.")
        return self


class TopologyServerShelfItem(CoreModel):
    """Beginner-facing saved-server card for topology assignment."""

    server_ref: ServerReferenceValue
    title: str
    host: str
    ssh_user: str = "root"
    ssh_port: int = 22
    validation_state: Literal["unknown", "validated", "key_ready", "failed"] = "unknown"
    capabilities: list[ServerCapability] = Field(default_factory=list)
    region: OptionalCountryCodeValue = ""
    last_checked_at: str = ""


class TopologyServerCapabilities(CoreModel):
    """Capabilities a saved server can provide to topology and routing plans."""

    server_ref: ServerReferenceValue
    capabilities: list[ServerCapability] = Field(min_length=1)
    region: OptionalCountryCodeValue = ""

    @field_validator("capabilities")
    @classmethod
    def reject_duplicate_capabilities(cls, capabilities: list[ServerCapability]) -> list[ServerCapability]:
        if len(capabilities) != len(dict.fromkeys(capabilities)):
            raise ValueError("List each server capability once.")
        return capabilities


class TrafficRouteRule(CoreModel):
    """One routing rule that maps traffic to an exit, optionally through a relay entry."""

    id: RequiredNameValue
    traffic: TrafficScope = "default"
    action: TrafficRouteAction = "route"
    country_codes: list[CountryCodeValue] = Field(default_factory=list)
    entry_server_ref: OptionalServerReferenceValue = ""
    exit_server_ref: OptionalServerReferenceValue = ""
    enabled: bool = True

    @model_validator(mode="after")
    def validate_traffic_scope(self) -> Self:
        if self.traffic == "country" and not self.country_codes:
            raise ValueError("Choose at least one country code for a country route.")
        if self.traffic == "default" and self.country_codes:
            raise ValueError("Country codes only apply to country routes.")
        if self.action == "route" and not self.exit_server_ref:
            raise ValueError("Choose an exit server for this route.")
        if self.action == "block":
            if self.traffic != "country":
                raise ValueError("Only country routes can be blocked.")
            if self.entry_server_ref or self.exit_server_ref:
                raise ValueError("Blocked routes cannot use entry or exit servers.")
        return self


class RegionalTrafficDecision(CoreModel):
    """UX-shaped decision for a country or region before it becomes route rules."""

    id: RequiredNameValue
    country_codes: list[CountryCodeValue] = Field(min_length=1)
    mode: RegionalTrafficMode = "block"
    entry_server_ref: OptionalServerReferenceValue = ""
    exit_server_ref: OptionalServerReferenceValue = ""

    @model_validator(mode="after")
    def validate_mode(self) -> Self:
        if self.mode == "regional_exit" and not self.exit_server_ref:
            raise ValueError("Choose where this regional traffic should exit.")
        if self.mode != "regional_exit" and (self.entry_server_ref or self.exit_server_ref):
            raise ValueError("Only regional-exit routing can choose entry or exit servers.")
        return self

    def to_route_rule(self) -> TrafficRouteRule | None:
        """Convert this UX decision into a route rule when it changes routing."""
        if self.mode == "default_exit":
            return None
        if self.mode == "block":
            return TrafficRouteRule(
                id=self.id,
                traffic="country",
                action="block",
                country_codes=self.country_codes,
            )
        return TrafficRouteRule(
            id=self.id,
            traffic="country",
            action="route",
            country_codes=self.country_codes,
            entry_server_ref=self.entry_server_ref,
            exit_server_ref=self.exit_server_ref,
        )


class TopologyBuilderDraft(CoreModel):
    """Beginner topology draft made of saved server cards and plain decisions."""

    servers: list[TopologyServerCapabilities] = Field(default_factory=list)
    regional_traffic: list[RegionalTrafficDecision] = Field(default_factory=list)

    def to_routing_policy(self) -> RoutingPolicyDraft:
        """Compile beginner decisions into the authoritative routing policy."""
        routes = [rule for decision in self.regional_traffic if (rule := decision.to_route_rule()) is not None]
        return RoutingPolicyDraft(servers=self.servers, routes=routes)


class RouteCard(CoreModel):
    """Plain-language route card for Studio and CLI review screens."""

    id: str
    enabled: bool
    action: TrafficRouteAction
    traffic_label: str
    entry_label: str = ""
    exit_label: str = ""
    sentence: str
    warnings: list[str] = Field(default_factory=list)


class RoutingPolicyDraft(CoreModel):
    """Draft topology policy where capabilities and routing are separate concepts."""

    servers: list[TopologyServerCapabilities] = Field(default_factory=list)
    routes: list[TrafficRouteRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_route_capabilities(self) -> Self:
        capabilities_by_ref: dict[str, set[ServerCapability]] = {}
        for server in self.servers:
            if server.server_ref in capabilities_by_ref:
                raise ValueError(f"Server {server.server_ref} is listed more than once.")
            capabilities_by_ref[server.server_ref] = set(server.capabilities)

        route_ids: set[str] = set()
        for route in self.routes:
            if route.id in route_ids:
                raise ValueError(f"Route {route.id} is listed more than once.")
            route_ids.add(route.id)
            if route.action == "block":
                continue
            self._require_capability(route.id, route.exit_server_ref, "exit", capabilities_by_ref)
            if route.entry_server_ref:
                self._require_capability(route.id, route.entry_server_ref, "relay", capabilities_by_ref)
        return self

    @staticmethod
    def _require_capability(
        route_id: str,
        server_ref: str,
        capability: ServerCapability,
        capabilities_by_ref: dict[str, set[ServerCapability]],
    ) -> None:
        capabilities = capabilities_by_ref.get(server_ref)
        if capabilities is None:
            raise ValueError(f"Route {route_id} references {server_ref}, but that server has no capabilities.")
        if capability not in capabilities:
            raise ValueError(f"Route {route_id} needs {server_ref} to have {capability} capability.")


def route_cards(policy: RoutingPolicyDraft) -> list[RouteCard]:
    """Render routing rules as beginner-readable cards without exposing YAML."""
    regions_by_ref = {server.server_ref: server.region for server in policy.servers}
    cards: list[RouteCard] = []
    for route in policy.routes:
        traffic_label = _traffic_label(route)
        warnings: list[str] = []
        if route.action == "block":
            sentence = f"{traffic_label} -> Blocked"
            cards.append(
                RouteCard(
                    id=route.id,
                    enabled=route.enabled,
                    action=route.action,
                    traffic_label=traffic_label,
                    sentence=sentence,
                    warnings=warnings,
                )
            )
            continue

        entry = route.entry_server_ref
        exit_ref = route.exit_server_ref
        if route.traffic == "country":
            exit_region = regions_by_ref.get(exit_ref, "")
            for country in route.country_codes:
                if exit_region and exit_region != country:
                    warnings.append(f"{country} traffic exits through {exit_region}, not {country}.")
        if entry and entry == exit_ref:
            sentence = f"{traffic_label} -> {exit_ref} regional exit"
        elif entry:
            sentence = f"{traffic_label} -> {entry} relay -> {exit_ref} exit"
        else:
            sentence = f"{traffic_label} -> {exit_ref} exit"
        cards.append(
            RouteCard(
                id=route.id,
                enabled=route.enabled,
                action=route.action,
                traffic_label=traffic_label,
                entry_label=entry,
                exit_label=exit_ref,
                sentence=sentence,
                warnings=warnings,
            )
        )
    return cards


def _traffic_label(route: TrafficRouteRule) -> str:
    if route.traffic == "default":
        return "Default traffic"
    return f"{', '.join(route.country_codes)} traffic"
