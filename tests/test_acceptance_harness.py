from __future__ import annotations

import os
import re
import subprocess
import tomllib
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from tests.realvm import orchestrator

_ROOT = Path(__file__).parents[1]


def _run_fake_systemlab_preflight(
    tmp_path: Path, failure: str = ""
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    docker_log = tmp_path / "docker.log"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"

if [[ "${1-}" == "buildx" && "${2-}" == "build" ]]; then
  cat >/dev/null
  if [[ "${FAKE_DOCKER_FAILURE-}" == "buildkit" ]]; then
    echo 'lookup registry-1.docker.io on [::1]:53: connection refused' >&2
    exit 1
  fi
fi

if [[ "${1-}" == "run" && "${FAKE_DOCKER_FAILURE-}" == "container" ]]; then
  exit 1
fi
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "FAKE_DOCKER_FAILURE": failure,
        "FAKE_DOCKER_LOG": str(docker_log),
    }
    result = subprocess.run(
        ["bash", "tests/systemlab/scripts/preflight.sh"],
        cwd=_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, docker_log.read_text(encoding="utf-8").splitlines()


def test_default_pytest_collection_includes_pure_systemlab_tests_only() -> None:
    config = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    excluded = set(config["tool"]["pytest"]["ini_options"]["norecursedirs"])

    assert "systemlab" not in excluded
    assert "realvm" in excluded


def test_systemlab_runtime_uses_canonical_documents_and_waited_pool_failover() -> None:
    subscription_test = (_ROOT / "tests/systemlab/scripts/test-subscription.py").read_text(encoding="utf-8")
    deploy = (_ROOT / "tests/systemlab/scripts/stages/10-deploy.sh").read_text(encoding="utf-8")
    resilience = (_ROOT / "tests/systemlab/scripts/stages/30-resilience.sh").read_text(encoding="utf-8")

    assert "fetch_canonical_xray" in subscription_test
    assert "except XrayStartupError" in subscription_test
    assert "build_reality_config" not in subscription_test
    assert "build_test_configs_from_cluster" not in subscription_test
    assert resilience.count("wait_for_command") >= 3
    assert "only exit B available" in resilience
    assert "only exit A available" in resilience
    assert "after both exits stop" in resilience
    assert "meridian-realm-*.service" in deploy
    assert "systemctl is-active meridian-relay" not in deploy


def test_systemlab_fast_rerun_retains_only_nested_images() -> None:
    makefile = (_ROOT / "Makefile").read_text(encoding="utf-8")
    controller = (_ROOT / "tests/systemlab/scripts/controller-run.sh").read_text(encoding="utf-8")
    cleanup = (_ROOT / "tests/systemlab/scripts/stages/90-cleanup.sh").read_text(encoding="utf-8")

    assert controller.count("90-cleanup.sh") >= 3
    assert "docker rm -f" in cleanup
    assert "docker volume prune -af" in cleanup
    assert "docker network prune -f" in cleanup
    assert "cleanup_failed" in cleanup
    assert "docker image" not in cleanup
    assert makefile.count("@set -eu") >= 2
    assert makefile.count("cleanup_status") >= 2


def test_systemlab_excludes_local_docker_state_from_git_and_build_context() -> None:
    gitignore = (_ROOT / ".gitignore").read_text(encoding="utf-8")
    dockerignore = (_ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "tests/systemlab/.docker-runtime/" in gitignore
    assert "tests/systemlab/.docker-runtime" in dockerignore
    assert "references/" in gitignore
    assert "references" in dockerignore


def test_systemlab_pebble_endpoint_uses_trusted_certificate_hostname() -> None:
    compose = yaml.safe_load((_ROOT / "tests/systemlab/compose.yml").read_text(encoding="utf-8"))
    base_image = (_ROOT / "tests/systemlab/images/base/Dockerfile").read_text(encoding="utf-8")

    controller_env = compose["services"]["controller"]["environment"]
    assert controller_env["MERIDIAN_ACME_SERVER"] == "https://pebble:14000/dir"
    assert "COPY tests/systemlab/fixtures/pebble-ca.pem /usr/local/share/ca-certificates/pebble-ca.crt" in base_image
    assert "RUN update-ca-certificates" in base_image


def test_systemlab_nested_docker_retains_bridge_nat() -> None:
    base_image = (_ROOT / "tests/systemlab/images/base/Dockerfile").read_text(encoding="utf-8")

    assert "default bridge NAT" in base_image
    assert '{"storage-driver":"vfs"}' in base_image
    assert '"iptables":false' not in base_image


def test_systemlab_fails_fast_on_missing_compose_daemon_or_build_dns() -> None:
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
    assert "docker buildx build" in preflight
    assert "# syntax=docker/dockerfile:1" in preflight
    assert "FROM scratch" in preflight
    assert "getent ahosts" in preflight
    assert "registry-1.docker.io" in preflight
    assert "colima start meridian --dns 192.168.5.2 --dns 1.1.1.1" in preflight
    assert "colima ssh -p meridian" in preflight
    assert "rm -f /etc/resolv.conf" in preflight
    assert "ARG TARGETARCH" in base_image
    assert 'if [ "$TARGETARCH" = "amd64" ]' in base_image
    assert "APT::Update::Error-Mode" in base_image
    assert "APT::Update::Error-Mode" in controller_image


def test_systemlab_preflight_exercises_buildkit_before_container_dns(tmp_path: Path) -> None:
    result, docker_calls = _run_fake_systemlab_preflight(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "BuildKit, and container DNS" in result.stdout
    build_call = next(index for index, call in enumerate(docker_calls) if call.startswith("buildx build"))
    run_call = next(index for index, call in enumerate(docker_calls) if call.startswith("run "))
    assert build_call < run_call


def test_systemlab_preflight_reports_buildkit_dns_before_container_probe(tmp_path: Path) -> None:
    result, docker_calls = _run_fake_systemlab_preflight(tmp_path, failure="buildkit")

    assert result.returncode == 2
    assert "lookup registry-1.docker.io on [::1]:53" in result.stderr
    assert "Docker BuildKit cannot load the external Dockerfile frontend" in result.stderr
    assert "dangling /etc/resolv.conf" in result.stderr
    assert not any(call.startswith("run ") for call in docker_calls)


def test_systemlab_preflight_distinguishes_container_dns_failure(tmp_path: Path) -> None:
    result, docker_calls = _run_fake_systemlab_preflight(tmp_path, failure="container")

    assert result.returncode == 2
    assert "Docker containers cannot resolve the package mirrors" in result.stderr
    assert "Docker BuildKit cannot load" not in result.stderr
    assert any(call.startswith("buildx build") for call in docker_calls)
    assert any(call.startswith("run ") for call in docker_calls)


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
