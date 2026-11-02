"""Optional checks of named policies with the pinned clients, without geodata downloads."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from meridian.control.formats.entries import SubscriptionUser
from meridian.control.models import ServiceSettings
from meridian.control.subscriptions import render_subscription
from meridian.core.delivery_policies import resolve_delivery_policy
from tests.test_control_subscription_corpus import FLEETS, SHORT_ID, UUID
from tests.test_delivery_policies import policy_data


def runtime_document(document: str, *, fake_ip: bool = False, ipv6: bool = False) -> str:
    data = policy_data()
    data["dns"]["fake_ip"] = fake_ip
    if ipv6:
        data["dns"]["resolvers"] = [{"transport": "plain", "address": "2001:db8::53"}]
    data["rules"].append(
        {
            "match": {"kind": "domain_suffix", "values": ["service.example.test"]},
            "action": {"kind": "exit", "exit": "west"},
            "network": "tcp",
            "ports": [{"start": 443}],
        }
    )
    settings = ServiceSettings.model_validate(
        {"profile_title": "Meridian", "delivery_policies": [data], "default_policy": "work"}
    )
    endpoints = [endpoint.model_copy(update={"exit_ref": "west"}) for endpoint in FLEETS["fleet"].endpoints]
    return render_subscription(
        SubscriptionUser(username="person", uuid=UUID, short_id=SHORT_ID),
        endpoints,
        document,
        profile_title="Meridian",
        delivery_policy=resolve_delivery_policy(settings),
    ).body


@pytest.mark.parametrize("ipv6", [False, True])
def test_xray_accepts_named_rules_and_dns_in_every_delivered_profile(
    xray_binary: Path, tmp_path: Path, ipv6: bool
) -> None:
    for index, config in enumerate(json.loads(runtime_document("xray-json", ipv6=ipv6))):
        path = tmp_path / f"profile-{index}.json"
        path.write_text(json.dumps(config))
        result = subprocess.run(
            [str(xray_binary), "run", "-test", "-config", str(path)],
            cwd=xray_binary.parent,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("fake_ip", [False, True])
@pytest.mark.parametrize("ipv6", [False, True])
def test_singbox_accepts_named_rules_exit_groups_and_dns(tmp_path: Path, fake_ip: bool, ipv6: bool) -> None:
    binary = os.environ.get("MERIDIAN_TEST_SINGBOX")
    if not binary:
        pytest.skip("set MERIDIAN_TEST_SINGBOX to validate with sing-box")
    path = tmp_path / "config.json"
    path.write_text(runtime_document("singbox", fake_ip=fake_ip, ipv6=ipv6))
    result = subprocess.run([binary, "check", "-c", str(path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("document", ["mihomo", "stash"])
@pytest.mark.parametrize("fake_ip", [False, True])
@pytest.mark.parametrize("ipv6", [False, True])
def test_mihomo_accepts_named_rules_and_dns_with_local_pinned_geodata(
    tmp_path: Path, document: str, fake_ip: bool, ipv6: bool
) -> None:
    binary = os.environ.get("MERIDIAN_TEST_MIHOMO")
    geodata = os.environ.get("MERIDIAN_TEST_MIHOMO_GEODATA")
    if not binary or not geodata:
        pytest.skip("set MERIDIAN_TEST_MIHOMO and MERIDIAN_TEST_MIHOMO_GEODATA; no data is downloaded")
    for filename in ("GeoSite.dat", "GeoIP.dat", "Country.mmdb"):
        source = Path(geodata) / filename
        if source.exists():
            shutil.copyfile(source, tmp_path / filename)
    path = tmp_path / "config.yaml"
    path.write_text(runtime_document(document, fake_ip=fake_ip, ipv6=ipv6))
    result = subprocess.run(
        [binary, "-t", "-d", str(tmp_path), "-f", str(path)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
