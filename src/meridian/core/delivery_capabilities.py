"""Client policy capabilities and omissions shared by renderers and previews."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import replace
from typing import Literal

from pydantic import Field

from meridian.core.delivery_matchers import DeliveryMatchKind, DnsResolver, DnsTransport
from meridian.core.delivery_policies import ResolvedDeliveryPolicy
from meridian.core.delivery_policy_ir import ClientRoute, policy_routes
from meridian.core.geodata_lists import SOURCE
from meridian.core.models import CoreModel
from meridian.core.subscription_formats import DocumentFormat

PolicyOmissionReason = Literal[
    "rules_not_carried",
    "dns_not_carried",
    "geosite_not_supported",
    "geoip_not_supported",
    "bittorrent_not_supported",
    "dns_transport_not_supported",
    "fake_ip_not_supported",
    "exit_unavailable",
]


class DeliveryFormatCapabilities(CoreModel):
    format: DocumentFormat
    matches: list[DeliveryMatchKind] = Field(max_length=8)
    dns_transports: list[DnsTransport] = Field(max_length=3)
    fake_ip: bool
    via_exit: bool
    bittorrent: bool
    local_geodata: bool


class DeliveryPolicyOmission(CoreModel):
    reason: PolicyOmissionReason
    rule: int | None = None
    values: list[str] = Field(default_factory=list, max_length=64)
    resolver: int | None = Field(default=None, ge=1, le=4)
    exit: str | None = None
    blocked: bool = False
    context: str | None = Field(default=None, max_length=512)


_MATCHES: list[DeliveryMatchKind] = [
    "domain_suffix",
    "domain_keyword",
    "domain_full",
    "geosite",
    "geoip",
    "cidr",
    "port",
    "network",
]


def delivery_capabilities(document: DocumentFormat) -> DeliveryFormatCapabilities:
    structured = document in {"xray-json", "mihomo", "stash", "singbox"}
    return DeliveryFormatCapabilities(
        format=document,
        matches=[match for match in _MATCHES if document != "singbox" or match not in {"geosite", "geoip"}]
        if structured
        else [],
        dns_transports=["doh", "plain"] if document == "xray-json" else ["doh", "dot", "plain"] if structured else [],
        fake_ip=document in {"mihomo", "singbox"},
        via_exit=structured,
        bittorrent=document == "xray-json",
        local_geodata=document in {"xray-json", "mihomo", "stash"},
    )


def represent_policy_routes(
    resolved: ResolvedDeliveryPolicy, document: DocumentFormat, *, available_exits: Collection[str] = ()
) -> tuple[tuple[ClientRoute, ...], list[DeliveryPolicyOmission]]:
    capabilities = delivery_capabilities(document)
    if not capabilities.matches:
        return (), [DeliveryPolicyOmission(reason="rules_not_carried")]
    routes = []
    omissions: list[DeliveryPolicyOmission] = []
    for route in policy_routes(resolved):
        if document == "singbox":
            omitted_domains = [value for value in route.domains if value.startswith("geosite:")]
            omitted_ips = [value for value in route.ips if value.startswith("geoip:") and value != "geoip:private"]
            if omitted_domains:
                omissions.append(
                    DeliveryPolicyOmission(reason="geosite_not_supported", rule=route.number, values=omitted_domains)
                )
            if omitted_ips:
                omissions.append(
                    DeliveryPolicyOmission(reason="geoip_not_supported", rule=route.number, values=omitted_ips)
                )
            route = replace(
                route,
                domains=tuple(value for value in route.domains if value not in omitted_domains),
                ips=tuple(value for value in route.ips if value not in omitted_ips),
            )
            # Never widen a removed matcher to its network/port conditions alone.
            if (omitted_domains or omitted_ips) and not (route.domains or route.ips):
                continue
        if route.protocols and not capabilities.bittorrent:
            omissions.append(
                DeliveryPolicyOmission(
                    reason="bittorrent_not_supported", rule=route.number, values=list(route.protocols)
                )
            )
            continue
        if route.action == "exit" and route.exit_ref not in available_exits:
            omissions.append(
                DeliveryPolicyOmission(reason="exit_unavailable", rule=route.number, exit=route.exit_ref, blocked=True)
            )
            route = replace(route, action="block", exit_ref="")
        routes.append(route)
    return tuple(routes), omissions


def represent_policy_dns(
    resolved: ResolvedDeliveryPolicy, document: DocumentFormat
) -> tuple[list[DnsResolver], bool, list[DeliveryPolicyOmission]]:
    dns = resolved.policy.dns
    if dns is None:
        return [], False, []
    capabilities = delivery_capabilities(document)
    if not capabilities.dns_transports:
        return [], False, [DeliveryPolicyOmission(reason="dns_not_carried")]
    resolvers: list[DnsResolver] = []
    omissions = []
    for number, resolver in enumerate(dns.resolvers, 1):
        if resolver.transport in capabilities.dns_transports:
            resolvers.append(resolver)
        else:
            omissions.append(
                DeliveryPolicyOmission(
                    reason="dns_transport_not_supported", resolver=number, values=[resolver.transport]
                )
            )
    if dns.fake_ip and not capabilities.fake_ip:
        omissions.append(DeliveryPolicyOmission(reason="fake_ip_not_supported"))
    return resolvers, dns.fake_ip and capabilities.fake_ip, omissions


GEODATA_SOURCE = SOURCE
