"""Bounded synthetic destinations and a pure, three-state first-match explanation."""

from __future__ import annotations

import ipaddress
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from meridian.core.delivery_capabilities import DeliveryPolicyOmission, represent_policy_routes
from meridian.core.delivery_geodata import GeoData, IpAddress
from meridian.core.delivery_matchers import DeliveryNetwork, domain_value
from meridian.core.delivery_policies import DeliveryAction, ResolvedDeliveryPolicy
from meridian.core.delivery_policy_ir import ClientRoute
from meridian.core.inputs import PortValue, TopologyIdValue, is_ip
from meridian.core.models import CoreModel
from meridian.core.subscription_formats import DocumentFormat

ExplanationReason = Literal[
    "rule_matched",
    "default_proxy",
    "geodata_unavailable",
    "port_required",
    "ip_required",
    "rules_not_carried",
    "no_connections",
    "inactive_person",
]


class DeliveryDestination(CoreModel):
    host: str = Field(min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    network: DeliveryNetwork = "tcp"
    protocol: Literal["other", "bittorrent"] = "other"


def parse_destination(
    value: str, *, port: int | None = None, network: DeliveryNetwork | None = None
) -> DeliveryDestination:
    try:
        if not value or len(value) > 512 or value != value.strip() or "%" in value:
            raise ValueError
        if is_ip(value):
            host, parsed_port, parsed_network = str(ipaddress.ip_address(value)), None, None
        else:
            explicit = "://" in value
            parsed = urlsplit(value if explicit else "tcp://" + value)
            if (
                parsed.scheme not in ("tcp", "udp")
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
                or parsed.password
            ):
                raise ValueError
            host = parsed.hostname or ""
            host = str(ipaddress.ip_address(host)) if is_ip(host) else domain_value(host)
            parsed_port = parsed.port
            parsed_network = parsed.scheme if explicit else None
        if parsed_port is not None and port is not None and parsed_port != port:
            raise ValueError
        if parsed_network is not None and network is not None and parsed_network != network:
            raise ValueError
        return DeliveryDestination(
            host=host,
            port=parsed_port if parsed_port is not None else port,
            network=network or ("udp" if parsed_network == "udp" else "tcp"),
        )
    except ValueError:
        raise ValueError(
            "Use a hostname or IP, optionally with a port or tcp:// or udp://. "
            "Paths, credentials, and conflicting port/network choices are not supported."
        ) from None


class DeliveryExplainRequest(CoreModel):
    destination: str = Field(min_length=1, max_length=512)
    person: str | None = Field(default=None, min_length=1, max_length=64)
    format: DocumentFormat = "xray-json"
    port: PortValue | None = None
    network: DeliveryNetwork | None = None
    protocol: Literal["other", "bittorrent"] = "other"

    @model_validator(mode="after")
    def valid_destination(self) -> Self:
        parse_destination(self.destination, port=self.port, network=self.network)
        return self

    @property
    def parsed(self) -> DeliveryDestination:
        parsed = parse_destination(self.destination, port=self.port, network=self.network)
        return DeliveryDestination.model_validate({**parsed.to_data(), "protocol": self.protocol})


class DeliveryExplanation(CoreModel):
    destination: DeliveryDestination
    format: DocumentFormat
    policy: TopologyIdValue
    status: Literal["matched", "default", "unknown", "unavailable"]
    action: DeliveryAction | None
    rule: int | None = Field(default=None, ge=1, le=133)
    reason: ExplanationReason
    omitted: list[DeliveryPolicyOmission] = Field(default_factory=list, max_length=512)
    geodata_available: bool


def explain_delivery_policy(
    resolved: ResolvedDeliveryPolicy,
    request: DeliveryExplainRequest,
    *,
    available_exits: set[str],
    geodata: GeoData | None = None,
    available: bool = True,
    active: bool = True,
) -> DeliveryExplanation:
    destination = request.parsed
    routes, omitted = represent_policy_routes(resolved, request.format, available_exits=available_exits)
    reason: ExplanationReason | None = None
    if not routes:
        reason = "rules_not_carried"
    elif not active:
        reason = "inactive_person"
    elif not available:
        reason = "no_connections"
    if reason is not None:
        return DeliveryExplanation(
            destination=destination,
            format=request.format,
            policy=resolved.policy.id,
            status="unavailable",
            action=None,
            reason=reason,
            omitted=omitted,
            geodata_available=geodata is not None,
        )
    # Xray's IPIfNonMatch scans names first. The generated TCP/UDP catch-all
    # always matches that pass, so it never reaches a second pass with DNS IPs.
    for route in routes:
        matched, missing = route_matches(route, destination, request.format, geodata)
        if matched is None:
            return DeliveryExplanation(
                destination=destination,
                format=request.format,
                policy=resolved.policy.id,
                status="unknown",
                action=None,
                rule=route.number,
                reason=missing or "geodata_unavailable",
                omitted=omitted,
                geodata_available=geodata is not None,
            )
        if matched:
            action = DeliveryAction(kind=route.action, exit=route.exit_ref or None)
            return DeliveryExplanation(
                destination=destination,
                format=request.format,
                policy=resolved.policy.id,
                status="matched",
                action=action,
                rule=route.number,
                reason="rule_matched",
                omitted=omitted,
                geodata_available=geodata is not None,
            )
    return DeliveryExplanation(
        destination=destination,
        format=request.format,
        policy=resolved.policy.id,
        status="default",
        action=DeliveryAction(kind="proxy"),
        reason="default_proxy",
        omitted=omitted,
        geodata_available=geodata is not None,
    )


def route_matches(
    route: ClientRoute,
    destination: DeliveryDestination,
    document: DocumentFormat,
    geodata: GeoData | None,
) -> tuple[bool | None, ExplanationReason | None]:
    if route.network and destination.network not in route.network.split(","):
        return False, None
    if route.protocols and destination.protocol not in route.protocols:
        return False, None
    port_unknown = bool(route.port) and destination.port is None
    if route.port and destination.port is not None:
        ranges = [part.split("-") for part in route.port.split(",")]
        if not any(int(parts[0]) <= destination.port <= int(parts[-1]) for parts in ranges):
            return False, None
    matches: list[bool | None] = []
    address = ipaddress.ip_address(destination.host) if is_ip(destination.host) else None
    missing: ExplanationReason | None = None
    for matcher in route.domains:
        if address is not None:
            matches.append(False)
            continue
        kind, _, value = matcher.partition(":")
        if kind == "domain":
            matches.append(destination.host == value or destination.host.endswith("." + value))
        elif kind == "full":
            matches.append(destination.host == value)
        elif kind == "keyword":
            matches.append(value in destination.host)
        else:
            matches.append(geodata.domain_matches(value, destination.host) if geodata is not None else None)
            missing = "geodata_unavailable"
    for matcher in route.ips:
        if address is None:
            matches.append(False)
        elif matcher == "geoip:private" and document == "singbox":
            matches.append(singbox_private(address))
        elif matcher.startswith("geoip:"):
            matches.append(
                geodata.ip_matches(matcher.removeprefix("geoip:"), [address]) if geodata is not None else None
            )
            missing = "geodata_unavailable"
        else:
            network = ipaddress.ip_network(matcher)
            matches.append(address.version == network.version and address in network)
    if matches and True not in matches:
        if None in matches:
            return None, missing
        return False, None
    if port_unknown:
        return None, "port_required"
    return True, None


def singbox_private(address: IpAddress) -> bool:
    # sing-box's non-global-unicast/private predicate follows Go netip, whose
    # documentation ranges remain unicast (Python is_private classifies more).
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    ranges = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16") if address.version == 4 else ("fc00::/7",)
    return (
        address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or str(address) == "255.255.255.255"
        or any(
            address.version == network.version and address in network for network in map(ipaddress.ip_network, ranges)
        )
    )
