"""Pure reading and matching of the stock Xray geosite/geoip protobuf data."""

from __future__ import annotations

import ipaddress
import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import TypeVar

IpAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IpNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
GeoIpList = tuple[tuple[IpNetwork, ...], bool]
_List = TypeVar("_List")
DECODED_LISTS = 16


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


def protobuf_spans(data: bytes, start: int = 0, end: int | None = None) -> Iterator[tuple[int, int, int, int]]:
    """Yield (field, value, start, end) without copying; length-delimited fields set start/end, varints the value."""
    index, end = start, len(data) if end is None else end
    while index < end:
        key, index = varint(data, index)
        number, wire = key >> 3, key & 7
        if not number:
            raise ValueError("Invalid geodata field.")
        if wire == 0:
            value, index = varint(data, index)
            yield number, value, index, index
        elif wire == 2:
            length, index = varint(data, index)
            if index + length > end:
                raise ValueError("Truncated geodata field.")
            yield number, -1, index, index + length
            index += length
        elif wire in (1, 5):
            index += 8 if wire == 1 else 4
            if index > end:
                raise ValueError("Truncated geodata field.")
        else:
            raise ValueError("Unsupported geodata field.")


def protobuf_fields(data: bytes) -> Iterator[tuple[int, int | bytes]]:
    for number, value, start, end in protobuf_spans(data):
        yield number, (value if value >= 0 else data[start:end])


class ProtobufLists(Mapping[str, _List]):
    """Index named geodata lists by offset and decode only the few an explanation asks for.

    Decoding every list at once exceeded the control service's memory limit.
    """

    def __init__(self, data: bytes, decode: Callable[[bytes], _List]) -> None:
        self._data = data
        self._decode = decode
        self._spans: dict[str, tuple[int, int]] = {}
        self._decoded: OrderedDict[str, _List] = OrderedDict()
        self._lock = threading.Lock()
        for field, _value, start, end in protobuf_spans(data):
            if field != 1 or start == end:
                continue
            name = next(
                (data[a:b].decode().lower() for n, v, a, b in protobuf_spans(data, start, end) if n == 1 and v < 0),
                "",
            )
            self._spans[name] = (start, end)

    def __getitem__(self, name: str) -> _List:
        start, end = self._spans[name]
        with self._lock:
            if name in self._decoded:
                self._decoded.move_to_end(name)
                return self._decoded[name]
            value = self._decode(self._data[start:end])
            self._decoded[name] = value
            if len(self._decoded) > DECODED_LISTS:
                self._decoded.popitem(last=False)
            return value

    def __iter__(self) -> Iterator[str]:
        return iter(self._spans)

    def __len__(self) -> int:
        return len(self._spans)


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


def _site_list(raw: bytes) -> tuple[GeoDomain, ...]:
    domains = []
    for number, value in protobuf_fields(raw):
        if number == 2 and isinstance(value, bytes):
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
    return tuple(domains)


def _ip_list(raw: bytes) -> GeoIpList:
    reverse, networks = False, []
    for number, value in protobuf_fields(raw):
        if number == 3 and isinstance(value, int):
            reverse = bool(value)
        elif number == 2 and isinstance(value, bytes):
            address, prefix = b"", 0
            for key, item in protobuf_fields(value):
                if key == 1 and isinstance(item, bytes):
                    address = item
                elif key == 2 and isinstance(item, int):
                    prefix = item
            networks.append(ipaddress.ip_network((ipaddress.ip_address(address), prefix), strict=True))
    return tuple(networks), reverse


@dataclass(frozen=True)
class GeoData:
    sites: Mapping[str, tuple[GeoDomain, ...]]
    ips: Mapping[str, GeoIpList]

    @classmethod
    def from_bytes(cls, geosite: bytes, geoip: bytes) -> GeoData:
        return cls(ProtobufLists(geosite, _site_list), ProtobufLists(geoip, _ip_list))

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
