from __future__ import annotations

import re
import tomllib
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from tests.realvm import orchestrator

_ROOT = Path(__file__).parents[1]


def test_default_pytest_collection_includes_pure_systemlab_tests_only() -> None:
    config = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    excluded = set(config["tool"]["pytest"]["ini_options"]["norecursedirs"])

    assert "systemlab" not in excluded
    assert "realvm" in excluded


def test_systemlab_runtime_uses_canonical_documents_and_waited_pool_failover() -> None:
    subscription_test = (_ROOT / "tests/systemlab/scripts/test-subscription.py").read_text(encoding="utf-8")
    resilience = (_ROOT / "tests/systemlab/scripts/stages/30-resilience.sh").read_text(encoding="utf-8")

    assert "fetch_canonical_xray" in subscription_test
    assert "build_reality_config" not in subscription_test
    assert "build_test_configs_from_cluster" not in subscription_test
    assert resilience.count("wait_for_command") >= 3
    assert "only exit B available" in resilience
    assert "only exit A available" in resilience
    assert "after both exits stop" in resilience


def test_systemlab_fails_fast_on_missing_compose_daemon_or_container_dns() -> None:
    makefile = (_ROOT / "Makefile").read_text(encoding="utf-8")
    preflight = (_ROOT / "tests/systemlab/scripts/preflight.sh").read_text(encoding="utf-8")
    base_image = (_ROOT / "tests/systemlab/images/base/Dockerfile").read_text(encoding="utf-8")
    controller_image = (_ROOT / "tests/systemlab/images/controller/Dockerfile").read_text(encoding="utf-8")

    assert "system-lab: system-lab-preflight" in makefile
    assert "system-lab-fast: system-lab-preflight" in makefile
    assert "docker compose version" in preflight
    assert "docker buildx version" in preflight
    assert "docker info" in preflight
    assert "docker compose -f tests/systemlab/compose.yml config" in preflight
    assert "getent ahosts" in preflight
    assert "colima start meridian --dns 192.168.5.2 --dns 1.1.1.1" in preflight
    assert "ARG TARGETARCH" in base_image
    assert 'if [ "$TARGETARCH" = "amd64" ]' in base_image
    assert "APT::Update::Error-Mode" in base_image
    assert "APT::Update::Error-Mode" in controller_image


def test_realvm_single_manifest_matches_implemented_checks() -> None:
    topology_path = _ROOT / "tests/realvm/topologies/single.yml"
    verify_path = _ROOT / "tests/realvm/verify/single.sh"
    topology = yaml.safe_load(topology_path.read_text(encoding="utf-8"))
    assert set(topology["verify"]) == {"tier_alpha"}
    declared = tuple(topology["verify"]["tier_alpha"])
    implemented = tuple(re.findall(r"^# VERIFY: ([a-z0-9_]+)$", verify_path.read_text(encoding="utf-8"), re.MULTILINE))

    assert declared == implemented
    assert declared == orchestrator.SUPPORTED_TOPOLOGIES["single"]
    assert "reality_xray_handshake" not in declared
    assert "bogus_uuid_rejected" not in declared
    orchestrator.require_supported_topology(orchestrator.load_topology("single"))


def test_realvm_rejects_unsupported_topology_before_provider_access(monkeypatch: pytest.MonkeyPatch) -> None:
    unsupported = orchestrator.Topology(
        name="chain-2hop",
        provider="hetzner",
        region="fsn1",
        size="cx23",
        image="ubuntu-24.04",
        nodes=[{"role": "relay"}, {"role": "exit"}],
    )
    provider_accessed = False

    monkeypatch.setattr(orchestrator, "load_topology", lambda _name: unsupported)

    def unexpected_provider_access(_name: str) -> None:
        nonlocal provider_accessed
        provider_accessed = True

    monkeypatch.setattr(orchestrator, "make_provider", unexpected_provider_access)

    with pytest.raises(SystemExit) as exc_info:
        orchestrator.cmd_up(Namespace(topology="chain-2hop", keep=False))

    assert exc_info.value.code == 2
    assert provider_accessed is False
    assert orchestrator.run_verification(unsupported, []) == 2
