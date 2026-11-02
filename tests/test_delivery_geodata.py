"""Geodata explanations use protobuf shapes and only verified local archives."""

from __future__ import annotations

import hashlib
import io
import ipaddress
import zipfile
from pathlib import Path

import pytest

from meridian.control.delivery_geodata import cached_geodata, read_geodata_archive
from meridian.core.delivery_explain import DeliveryExplainRequest, explain_delivery_policy
from meridian.core.delivery_geodata import GeoData
from meridian.core.delivery_policies import DeliveryPolicySettings, resolve_delivery_policy
from meridian.core.xray_pins import XRAY_SHA256


def integer(value: int) -> bytes:
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def field(number: int, value: str | bytes | int) -> bytes:
    if isinstance(value, int):
        return integer(number << 3) + integer(value)
    body = value.encode() if isinstance(value, str) else value
    return integer((number << 3) | 2) + integer(len(body)) + body


def dataset() -> tuple[bytes, bytes]:
    sites = field(1, field(1, "PRIVATE") + field(2, field(1, 2) + field(2, "lan")))
    sites += field(1, field(1, "CATEGORY-RU") + field(2, field(1, 3) + field(2, "domestic.example.test")))
    ips = field(1, field(1, "PRIVATE"))
    ips += field(1, field(1, "RU") + field(2, field(1, ipaddress.ip_address("198.51.100.0").packed) + field(2, 24)))
    return sites, ips


@pytest.mark.parametrize(
    "destination, rule", [("router.lan:443", 1), ("domestic.example.test:443", 2), ("198.51.100.10:443", 3)]
)
def test_pinned_domain_and_ip_lists_explain_the_correct_preset_rule(destination: str, rule: int) -> None:
    data = GeoData.from_bytes(*dataset())
    result = explain_delivery_policy(
        resolve_delivery_policy(DeliveryPolicySettings()),
        DeliveryExplainRequest(destination=destination),
        available_exits={"west"},
        geodata=data,
    )
    assert result.status == "matched" and result.action.kind == "direct" and result.rule == rule


def test_cached_data_must_match_a_pin_and_never_starts_a_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data in zip(("geosite.dat", "geoip.dat"), dataset(), strict=True):
            archive.writestr(name, data)
    blob = stream.getvalue()
    digest = hashlib.sha256(blob).hexdigest()
    monkeypatch.setitem(XRAY_SHA256, "test.zip", digest)
    path = tmp_path / digest
    path.write_bytes(blob)
    assert cached_geodata(tmp_path).domain_matches("private", "router.lan")
    assert read_geodata_archive(path, "0" * 64, 0, len(blob)) is None
    path.write_bytes(b"bad archive")
    assert cached_geodata(tmp_path) is None


@pytest.mark.parametrize("data", [b"\x0a\xff", b"\x00", b"\x0a\x10bad"])
def test_truncated_geodata_is_refused(data: bytes) -> None:
    with pytest.raises(ValueError):
        GeoData.from_bytes(data, b"")
