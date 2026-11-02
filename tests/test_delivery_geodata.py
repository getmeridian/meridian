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


@pytest.mark.parametrize(
    "attributes", [("!cn",), ("cn",), (), ("ads",), ("cn", "!cn")], ids=["literal", "cn", "untagged", "other", "both"]
)
@pytest.mark.parametrize("document", ["xray-json", "mihomo"])
def test_geosite_attributes_select_literal_keys_in_the_data(attributes: tuple[str, ...], document: str) -> None:
    domain = field(1, 3) + field(2, "service.example.test")
    domain += b"".join(field(3, field(1, attribute)) for attribute in attributes)
    data = GeoData.from_bytes(
        field(1, field(1, "PRIVATE")) + field(1, field(1, "CATEGORY-COMPANIES") + field(2, domain)), b""
    )
    assert data.domain_matches("category-companies@!cn", "service.example.test") is ("!cn" in attributes)
    assert data.domain_matches("category-companies@cn", "service.example.test") is ("cn" in attributes)
    settings = DeliveryPolicySettings.model_validate(
        {
            "delivery_policies": [
                {
                    "id": "work",
                    "rules": [
                        {
                            "match": {"kind": "geosite", "values": ["category-companies@!cn"]},
                            "action": {"kind": "block"},
                        }
                    ],
                }
            ],
            "default_policy": "work",
        }
    )
    result = explain_delivery_policy(
        resolve_delivery_policy(settings),
        DeliveryExplainRequest.model_validate({"destination": "service.example.test:443", "format": document}),
        available_exits={"west"},
        geodata=data,
    )
    assert result.action is not None
    assert (result.status, result.action.kind, result.rule) == (
        ("matched", "block", 2) if "!cn" in attributes else ("default", "proxy", None)
    )


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


def test_geodata_decodes_only_the_lists_an_explanation_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Decoding every pinned list at once took over 400 MB and outgrew the control service's memory limit."""
    from meridian.core import delivery_geodata

    sites, ips = dataset()
    sites += b"".join(
        field(1, field(1, f"LIST-{n}") + field(2, field(1, 2) + field(2, f"{n}.test"))) for n in range(40)
    )
    decoded: list[bytes] = []
    real = delivery_geodata._site_list
    monkeypatch.setattr(delivery_geodata, "_site_list", lambda raw: decoded.append(raw) or real(raw))
    data = GeoData.from_bytes(sites, ips)
    assert len(data.sites) == 42 and not decoded
    assert data.domain_matches("list-7", "www.7.test") is True
    assert data.domain_matches("list-7", "8.test") is False
    assert len(decoded) == 1
    for n in range(40):
        data.domain_matches(f"list-{n}", "x.test")
    assert len(data.sites._decoded) == delivery_geodata.DECODED_LISTS  # type: ignore[attr-defined]
    assert data.domain_matches("missing", "x.test") is None


def test_pinned_geodata_stays_small_in_memory() -> None:
    import os
    import tracemalloc

    from tests.systemlab.control_memory import EXPLANATION_DESTINATIONS

    binary = os.environ.get("MERIDIAN_TEST_XRAY")
    folder = Path(binary).parent if binary else None
    if folder is None or not (folder / "geosite.dat").is_file() or not (folder / "geoip.dat").is_file():
        pytest.skip("pinned geodata beside MERIDIAN_TEST_XRAY is not available")
    tracemalloc.start()
    try:
        data = GeoData.from_bytes((folder / "geosite.dat").read_bytes(), (folder / "geoip.dat").read_bytes())
        resolved = resolve_delivery_policy(DeliveryPolicySettings())
        for destination, status, action in (
            *((destination, "default", "proxy") for destination in EXPLANATION_DESTINATIONS),
            ("router.lan:443", "matched", "direct"),
            ("198.51.100.10:443", "matched", "direct"),
        ):
            result = explain_delivery_policy(
                resolved,
                DeliveryExplainRequest(destination=destination),
                available_exits={"west"},
                geodata=data,
            )
            assert result.geodata_available and result.action is not None
            assert (result.status, result.action.kind) == (status, action)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert {"private", "ru"} <= set(data.ips._decoded)  # type: ignore[attr-defined]
    print(f"pinned geodata explanation peak: {peak / 1024**2:.1f} MiB ({peak} bytes)")
    assert peak < 64 * 1024 * 1024, f"Pinned geodata explanation used {peak / 1024**2:.1f} MiB; budget is 64 MiB."
