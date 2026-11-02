"""Finite client matchers and DNS choices, validated before review or serialization."""

from __future__ import annotations

import ipaddress
import re
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, model_validator

from meridian.core.geodata_lists import GEOIP_LISTS, GEOSITE_LISTS
from meridian.core.inputs import PortValue, validate_domain_suffix_value, validate_optional_transport_path_value
from meridian.core.models import CoreModel

DeliveryMatchKind = Literal[
    "domain_suffix", "domain_keyword", "domain_full", "geosite", "geoip", "cidr", "port", "network"
]
DeliveryNetwork = Literal["tcp", "udp"]
DnsTransport = Literal["doh", "dot", "plain"]


def domain_value(value: str) -> str:
    return validate_domain_suffix_value(value).lower()


def keyword_value(value: str) -> str:
    if re.fullmatch(r"[a-zA-Z0-9._-]{1,80}", value) is None:
        raise ValueError("Use an ASCII domain fragment with letters, digits, dots, hyphens, or underscores.")
    return value.lower()


def cidr_value(value: str) -> str:
    try:
        if "/" not in value or "%" in value:
            raise ValueError
        return str(ipaddress.ip_network(value, strict=True))
    except ValueError:
        raise ValueError("Enter a CIDR network address, such as 198.51.100.0/24, without host bits.") from None


def geosite_value(value: str) -> str:
    return geo_value(value, "geosite", GEOSITE_LISTS)


def geoip_value(value: str) -> str:
    return geo_value(value, "geoip", GEOIP_LISTS)


def geo_value(value: str, kind: str, choices: frozenset[str]) -> str:
    name = value.lower()
    if name not in choices:
        raise ValueError(f"Unknown {kind} list. Choose a list in Meridian's pinned geodata.")
    return name


def resolver_address(value: str) -> str:
    try:
        if "%" in value:
            raise ValueError
        address = ipaddress.ip_address(value)
        if address.is_unspecified or address.is_multicast or str(address) == "255.255.255.255":
            raise ValueError
        return str(address)
    except ValueError:
        raise ValueError("Enter a unicast DNS resolver IP address. Hostnames need a bootstrap resolver.") from None


def doh_path(value: str) -> str:
    normalized = validate_optional_transport_path_value(value)
    if not value.startswith("/") or value != "/" + normalized:
        raise ValueError("Start the DoH path with /; query strings and credentials are not supported.")
    return value


DomainValue = Annotated[str, Field(min_length=1, max_length=253), AfterValidator(domain_value)]
KeywordValue = Annotated[
    str, Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._-]+$"), AfterValidator(keyword_value)
]
CidrValue = Annotated[str, Field(min_length=3, max_length=49), AfterValidator(cidr_value)]
GeositeValue = Annotated[str, Field(min_length=1, max_length=64), AfterValidator(geosite_value)]
GeoipValue = Annotated[str, Field(min_length=1, max_length=64), AfterValidator(geoip_value)]
ResolverAddress = Annotated[str, Field(min_length=3, max_length=45), AfterValidator(resolver_address)]
DohPath = Annotated[str, Field(min_length=1, max_length=200), AfterValidator(doh_path)]


class DeliveryPortRange(CoreModel):
    start: PortValue
    end: PortValue | None = None

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end is None:
            object.__setattr__(self, "end", self.start)
        elif self.end < self.start:
            raise ValueError("The end port must be at least the start port.")
        return self

    def contains(self, port: int) -> bool:
        return self.start <= port <= (self.end or self.start)

    def text(self) -> str:
        return str(self.start) if self.end == self.start else f"{self.start}-{self.end}"


class DomainSuffixMatch(CoreModel):
    kind: Literal["domain_suffix"]
    values: list[DomainValue] = Field(min_length=1, max_length=64)


class DomainKeywordMatch(CoreModel):
    kind: Literal["domain_keyword"]
    values: list[KeywordValue] = Field(min_length=1, max_length=64)


class DomainFullMatch(CoreModel):
    kind: Literal["domain_full"]
    values: list[DomainValue] = Field(min_length=1, max_length=64)


class GeositeMatch(CoreModel):
    kind: Literal["geosite"]
    values: list[GeositeValue] = Field(min_length=1, max_length=64)


class GeoipMatch(CoreModel):
    kind: Literal["geoip"]
    values: list[GeoipValue] = Field(min_length=1, max_length=64)


class CidrMatch(CoreModel):
    kind: Literal["cidr"]
    values: list[CidrValue] = Field(min_length=1, max_length=64)


class PortMatch(CoreModel):
    kind: Literal["port"]
    values: list[DeliveryPortRange] = Field(min_length=1, max_length=16)


class NetworkMatch(CoreModel):
    kind: Literal["network"]
    values: list[DeliveryNetwork] = Field(min_length=1, max_length=2)


DeliveryMatch = Annotated[
    DomainSuffixMatch
    | DomainKeywordMatch
    | DomainFullMatch
    | GeositeMatch
    | GeoipMatch
    | CidrMatch
    | PortMatch
    | NetworkMatch,
    Field(discriminator="kind"),
]


class DohResolver(CoreModel):
    transport: Literal["doh"]
    address: ResolverAddress
    port: PortValue = 443
    path: DohPath = "/dns-query"


class DotResolver(CoreModel):
    transport: Literal["dot"]
    address: ResolverAddress
    port: PortValue = 853


class PlainResolver(CoreModel):
    transport: Literal["plain"]
    address: ResolverAddress
    port: PortValue = 53
    protocol: DeliveryNetwork = "udp"


DnsResolver = Annotated[DohResolver | DotResolver | PlainResolver, Field(discriminator="transport")]


class DeliveryDns(CoreModel):
    resolvers: list[DnsResolver] = Field(min_length=1, max_length=4)
    fake_ip: bool = False

    @model_validator(mode="after")
    def unique(self) -> Self:
        values = [resolver.model_dump_json() for resolver in self.resolvers]
        if len(values) != len(set(values)):
            raise ValueError("List each DNS resolver once.")
        return self


def resolver_uri(resolver: DnsResolver) -> str:
    address = f"[{resolver.address}]" if ":" in resolver.address else resolver.address
    if isinstance(resolver, DohResolver):
        port = "" if resolver.port == 443 else f":{resolver.port}"
        return f"https://{address}{port}{resolver.path}"
    if isinstance(resolver, DotResolver):
        return f"tls://{address}:{resolver.port}"
    if resolver.protocol == "tcp":
        return f"tcp://{address}:{resolver.port}"
    return address if resolver.port == 53 else f"udp://{address}:{resolver.port}"
