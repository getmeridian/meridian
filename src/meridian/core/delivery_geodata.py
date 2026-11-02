"""Pure reading and matching of the stock Xray geosite/geoip protobuf data."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterator
from dataclasses import dataclass

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def varint(data: bytes, index: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if index >= len(data):
            break
        byte = data[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, index
    raise ValueError("Invalid geodata integer.")


def protobuf_fields(data: bytes) -> Iterator[tuple[int, int | bytes]]:
    index = 0
    while index < len(data):
        key, index = varint(data, index)
        number, wire = key >> 3, key & 7
        if not number:
            raise ValueError("Invalid geodata field.")
        if wire == 0:
            value, index = varint(data, index)
            yield number, value
        elif wire == 2:
            length, index = varint(data, index)
            if index + length > len(data):
                raise ValueError("Truncated geodata field.")
            yield number, data[index : index + length]
            index += length
        elif wire in (1, 5):
            index += 8 if wire == 1 else 4
            if index > len(data):
                raise ValueError("Truncated geodata field.")
        else:
            raise ValueError("Unsupported geodata field.")


@dataclass(frozen=True)
class GeoDomain:
    kind: int
    value: str
    attributes: frozenset[str] = frozenset()

    def matches(self, domain: str) -> bool | None:
        if self.kind == 0:
            return self.value.lower() in domain
        if self.kind == 2:
            return domain == self.value.lower() or domain.endswith("." + self.value.lower())
        if self.kind == 3:
            return domain == self.value.lower()
        if self.kind == 1:
            # Patterns come from a verified pinned archive, never from operator input.
            try:
                return re.search(self.value, domain, re.IGNORECASE) is not None
            except re.error:
                return None
        return None


@dataclass(frozen=True)
class GeoData:
    sites: dict[str, tuple[GeoDomain, ...]]
    ips: dict[str, tuple[tuple[IpNetwork, ...], bool]]

    @classmethod
    def from_bytes(cls, geosite: bytes, geoip: bytes) -> GeoData:
        sites: dict[str, tuple[GeoDomain, ...]] = {}
        ips: dict[str, tuple[tuple[IpNetwork, ...], bool]] = {}
        for field, raw in protobuf_fields(geosite):
            if field != 1 or not isinstance(raw, bytes):
                continue
            name, domains = "", []
            for number, value in protobuf_fields(raw):
                if number == 1 and isinstance(value, bytes):
                    name = value.decode().lower()
                elif number == 2 and isinstance(value, bytes):
                    kind, text = 0, ""
                    attributes: set[str] = set()
                    for key, item in protobuf_fields(value):
                        if key == 1 and isinstance(item, int):
                            kind = item
                        elif key == 2 and isinstance(item, bytes):
                            text = item.decode()
                        elif key == 3 and isinstance(item, bytes):
                            attributes.update(
                                attribute.decode().lower()
                                for n, attribute in protobuf_fields(item)
                                if n == 1 and isinstance(attribute, bytes)
                            )
                    domains.append(GeoDomain(kind, text, frozenset(attributes)))
            sites[name] = tuple(domains)
        for field, raw in protobuf_fields(geoip):
            if field != 1 or not isinstance(raw, bytes):
                continue
            name, reverse, networks = "", False, []
            for number, value in protobuf_fields(raw):
                if number == 1 and isinstance(value, bytes):
                    name = value.decode().lower()
                elif number == 3 and isinstance(value, int):
                    reverse = bool(value)
                elif number == 2 and isinstance(value, bytes):
                    address, prefix = b"", 0
                    for key, item in protobuf_fields(value):
                        if key == 1 and isinstance(item, bytes):
                            address = item
                        elif key == 2 and isinstance(item, int):
                            prefix = item
                    networks.append(ipaddress.ip_network((ipaddress.ip_address(address), prefix), strict=True))
            ips[name] = (tuple(networks), reverse)
        return cls(sites, ips)

    def domain_matches(self, name: str, domain: str) -> bool | None:
        entries = self.sites.get(name)
        if entries is None:
            base, _, attribute = name.partition("@")
            entries = self.sites.get(base)
            if entries is None:
                return None
            if attribute:
                entries = tuple(entry for entry in entries if attribute in entry.attributes)
        matches = [entry.matches(domain) for entry in entries]
        return True if True in matches else None if None in matches else False

    def ip_matches(self, name: str, addresses: list[IpAddress]) -> bool | None:
        entry = self.ips.get(name)
        if entry is None:
            return None
        networks, inverse = entry
        matches = any(
            address.version == network.version and address in network for address in addresses for network in networks
        )
        return not matches if inverse else matches
