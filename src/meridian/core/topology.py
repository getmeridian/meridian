"""Topology intent contracts: roles, protocol paths, routes, access, and delivery."""

from __future__ import annotations

import ipaddress
import re
from typing import Final, Literal, Self

from pydantic import Field, HttpUrl, ValidationInfo, field_validator, model_validator

from meridian.core.inputs import (
    AccessUsernameValue,
    OptionalCountryCodeValue,
    OptionalHostnameValue,
    OptionalTopologyIdValue,
    OptionalTransportPathValue,
    PortValue,
    ServerReferenceValue,
    ServerTitleValue,
    TopologyIdValue,
    validate_country_code_value,
    validate_hostname_value,
)
from meridian.core.models import CoreModel

ServerCapability = Literal["panel", "exit", "relay", "routing_gateway"]
TrafficRouteAction = Literal["route", "block"]
ProtocolKind = Literal["reality", "xhttp", "wss", "hysteria2"]
# Subscription order: the most censorship-resistant TCP path first, the UDP fallback last.
PROTOCOL_PRIORITY: Final[tuple[ProtocolKind, ...]] = ("reality", "xhttp", "wss", "hysteria2")
TlsFingerprint = Literal["chrome", "firefox", "safari", "ios", "android", "edge", "random", "randomized"]
# uTLS ClientHello presented on every TCP path unless the operator picks another.
DEFAULT_TLS_FINGERPRINT: Final[TlsFingerprint] = "chrome"
# Direct-to-server TLS paths are also published with these ClientHellos, so one blocked
# fingerprint leaves another entry working. CDN and QUIC paths keep their single fingerprint.
FINGERPRINT_COPIES: Final[tuple[TlsFingerprint, ...]] = ("chrome", "firefox")
FINGERPRINT_COPY_PROTOCOLS: Final[frozenset[str]] = frozenset({"reality", "xhttp"})


def path_fingerprints(protocol: str, primary: TlsFingerprint) -> list[TlsFingerprint]:
    """The path's own fingerprint first, then each missing copy for direct TLS paths."""
    if protocol not in FINGERPRINT_COPY_PROTOCOLS:
        return [primary]
    return [primary, *(fingerprint for fingerprint in FINGERPRINT_COPIES if fingerprint != primary)]


RouteMatchKind = Literal["all", "country", "domain", "ip", "network"]
EgressStrategy = Literal["least_ping"]
EgressOverrideMatch = Literal["domain", "geosite", "geoip"]
EgressOverrideAction = Literal["warp", "direct", "block"]
# v2fly geosite/geoip list names: "netflix", "category-ru", "geolocation-!cn", "google@cn".
_GEO_LIST_NAME = re.compile(r"[a-z0-9][a-z0-9!@._-]{0,63}")
DeliveryFormat = Literal["base64", "xray_json", "mihomo", "singbox"]


def _default_delivery_formats() -> list[DeliveryFormat]:
    return ["base64", "xray_json", "mihomo", "singbox"]


class ControlPlaneIntent(CoreModel):
    """One server that owns the Remnawave control-plane workload."""

    server_ref: ServerReferenceValue
    public_hostname: OptionalHostnameValue = ""
    title: ServerTitleValue = "Meridian"


class ProtocolPathIntent(CoreModel):
    """One protocol-specific public path backed by an exit workload."""

    id: TopologyIdValue
    protocol: ProtocolKind
    listen_port: int = Field(default=0, ge=0, le=65535)
    public_port: PortValue = 443
    reality_sni: OptionalHostnameValue = ""
    tls_sni: OptionalHostnameValue = ""
    host: OptionalHostnameValue = ""
    path: OptionalTransportPathValue = ""
    fingerprint: TlsFingerprint = DEFAULT_TLS_FINGERPRINT

    @model_validator(mode="after")
    def validate_protocol_fields(self) -> Self:
        if self.protocol == "hysteria2" and self.fingerprint != DEFAULT_TLS_FINGERPRINT:
            raise ValueError("Hysteria2 runs over QUIC; a TLS fingerprint does not apply.")
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


class EgressOverrideIntent(CoreModel):
    """Send matching destinations through WARP, directly, or nowhere, ahead of the exit's default egress."""

    id: TopologyIdValue
    match: EgressOverrideMatch
    values: list[str] = Field(min_length=1)
    action: EgressOverrideAction

    @field_validator("values")
    @classmethod
    def validate_values(cls, values: list[str], info: ValidationInfo) -> list[str]:
        kind = info.data.get("match", "domain")
        normalized: list[str] = []
        for value in values:
            if kind == "domain":
                normalized.append(validate_hostname_value(value))
                continue
            name = value.strip().lower().removeprefix(f"{kind}:")
            if _GEO_LIST_NAME.fullmatch(name) is None:
                raise ValueError(f"Enter a {kind} list name such as {'netflix' if kind == 'geosite' else 'ir'}.")
            normalized.append(name)
        if len(normalized) != len(set(normalized)):
            raise ValueError(f"{kind} override values must be unique.")
        return normalized


class ExitIntent(CoreModel):
    """One independent Xray exit workload and its public protocol paths."""

    id: TopologyIdValue
    server_ref: ServerReferenceValue
    region: OptionalCountryCodeValue = ""
    paths: list[ProtocolPathIntent] = Field(min_length=1)
    warp: bool = False
    geo_block: bool = False  # refuse Russian destinations (geosite:category-ru, geoip:ru)
    egress_overrides: list[EgressOverrideIntent] = Field(default_factory=list)

    @property
    def needs_warp(self) -> bool:
        """WARP runs on this exit when it is the default egress or any override uses it."""
        return self.warp or any(override.action == "warp" for override in self.egress_overrides)

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        override_ids = [override.id for override in self.egress_overrides]
        if len(override_ids) != len(set(override_ids)):
            raise ValueError(f"Exit {self.id} lists an egress override ID more than once.")
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

    id: TopologyIdValue
    hop_server_refs: list[ServerReferenceValue] = Field(min_length=1)
    exit_ref: TopologyIdValue
    protocol_path_ref: TopologyIdValue
    listen_port: PortValue = 443
    reality_sni: OptionalHostnameValue = ""

    @field_validator("hop_server_refs")
    @classmethod
    def reject_duplicate_hops(cls, hops: list[str]) -> list[str]:
        if len(hops) != len(set(hops)):
            raise ValueError("A transparent relay chain cannot visit the same server twice.")
        return hops


class RoutingGatewayIntent(CoreModel):
    """A smart entry workload using a reviewed Reality path as its public blueprint."""

    id: TopologyIdValue
    server_ref: ServerReferenceValue
    bridge_path_ref: TopologyIdValue


class EgressPoolIntent(CoreModel):
    """An ordered set of exits used for server-side failover."""

    id: TopologyIdValue
    exit_refs: list[TopologyIdValue] = Field(min_length=1)
    strategy: EgressStrategy = "least_ping"
    fail_closed: bool = True
    probe_url: HttpUrl = HttpUrl("https://www.apple.com/library/test/success.html")

    @model_validator(mode="after")
    def validate_pool(self) -> Self:
        if len(self.exit_refs) != len(set(self.exit_refs)):
            raise ValueError(f"Egress pool {self.id} lists an exit more than once.")
        if not self.fail_closed:
            raise ValueError("Meridian egress pools must fail closed when every exit is down.")
        return self


class OrderedRouteIntent(CoreModel):
    """One explicit first-match routing rule."""

    id: TopologyIdValue
    priority: int = Field(ge=0)
    match: RouteMatchKind = "all"
    match_values: list[str] = Field(default_factory=list)
    action: TrafficRouteAction = "route"
    target_ref: OptionalTopologyIdValue = ""
    source_gateway_ref: OptionalTopologyIdValue = ""
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
    users: list[AccessUsernameValue] = Field(min_length=1)

    @field_validator("users")
    @classmethod
    def reject_duplicate_users(cls, users: list[str]) -> list[str]:
        if len(users) != len(set(users)):
            raise ValueError("Access usernames must be unique.")
        return users


class DeliveryIntent(CoreModel):
    """Canonical Remnawave subscription presentation settings."""

    profile_title: ServerTitleValue = "Meridian"
    template_name: TopologyIdValue = "meridian"
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
    default_egress_ref: TopologyIdValue
    access: AccessIntent
    delivery: DeliveryIntent = Field(default_factory=DeliveryIntent)
    harden: bool = True  # SSH key-only auth + fail2ban; off when other services share the servers
    # Pass each client's real address to Xray and the panel (PROXY protocol, X-Forwarded-For).
    # Off by default: Remnawave then never stores where users connect from.
    forward_client_ips: bool = False

    def exits_in_delivery_order(self) -> list[ExitIntent]:
        """Exits as clients see them: the control server's first, then the rest by ID."""
        return sorted(self.exits, key=lambda item: (item.server_ref != self.control.server_ref, item.id))

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
        if self.routes and not gateways:
            raise ValueError("Server-side routing rules require at least one routing gateway.")
        if self.default_egress_ref not in valid_targets:
            raise ValueError(f"Default egress {self.default_egress_ref!r} does not name an exit or pool.")

        for pool in self.egress_pools:
            missing = [exit_ref for exit_ref in pool.exit_refs if exit_ref not in exits]
            if missing:
                raise ValueError(f"Egress pool {pool.id} references unknown exits: {', '.join(missing)}.")

        gateway_servers = [gateway.server_ref for gateway in self.routing_gateways]
        if len(gateway_servers) != len(set(gateway_servers)):
            raise ValueError("Each routing gateway requires its own server.")
        exit_servers = {exit_.server_ref for exit_ in self.exits}
        reused_servers = sorted(exit_servers & set(gateway_servers))
        if reused_servers:
            raise ValueError(
                "A server cannot run both an exit Profile and a routing-gateway Profile: "
                + ", ".join(reused_servers)
                + "."
            )
        paths_by_id: dict[str, list[ProtocolPathIntent]] = {}
        for exit_ in self.exits:
            for path in exit_.paths:
                paths_by_id.setdefault(path.id, []).append(path)
        for gateway in self.routing_gateways:
            matches = paths_by_id.get(gateway.bridge_path_ref, [])
            if len(matches) != 1:
                raise ValueError(f"Gateway {gateway.id} must reference one globally unique protocol path blueprint.")
            if matches[0].protocol != "reality":
                raise ValueError("Routing gateways currently require a Reality entry path.")

        for relay in self.transparent_relays:
            relay_exit = exits.get(relay.exit_ref)
            if relay_exit is None:
                raise ValueError(f"Relay {relay.id} references unknown exit {relay.exit_ref!r}.")
            relay_path = next(
                (candidate for candidate in relay_exit.paths if candidate.id == relay.protocol_path_ref),
                None,
            )
            if relay_path is None:
                raise ValueError(
                    f"Relay {relay.id} references unknown path {relay.protocol_path_ref!r} on exit {relay.exit_ref}."
                )
            if relay_path.protocol == "hysteria2":
                raise ValueError("Transparent Realm chains cannot relay Hysteria2 or other UDP paths.")
            if relay.reality_sni and relay_path.protocol != "reality":
                raise ValueError("A custom relay Reality SNI can only target a Reality path.")
            if relay_exit.server_ref in relay.hop_server_refs:
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
        catch_all_priorities = [route.priority for route in self.routes if route.enabled and route.match == "all"]
        if catch_all_priorities and max(route.priority for route in self.routes if route.enabled) > min(
            catch_all_priorities
        ):
            raise ValueError("Catch-all routing rules must come after every more specific rule.")
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
