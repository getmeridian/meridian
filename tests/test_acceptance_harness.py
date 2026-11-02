from __future__ import annotations

import os
import re
import shlex
import subprocess
import tomllib
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from tests.realvm import orchestrator
from tests.realvm.providers.base import VMInstance

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

if [[ "${1-}" == "pull" ]]; then
  if [[ "${FAKE_DOCKER_FAILURE-}" == "daemon" ]]; then
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
        "DOCKER_HOST": "",
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
    assert "netfwd-*.service" in deploy
    assert "systemctl is-active meridian-relay" not in deploy


def test_systemlab_make_targets_build_the_agent_and_always_tear_down() -> None:
    makefile = (_ROOT / "Makefile").read_text(encoding="utf-8")

    for target in ("system-lab:", "system-lab-fast:"):
        prerequisites = next(line for line in makefile.splitlines() if line.startswith(target))
        assert "system-lab-agent" in prerequisites
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


def test_systemlab_nodes_run_the_agent_without_nested_docker() -> None:
    base_image = (_ROOT / "tests/systemlab/images/base/Dockerfile").read_text(encoding="utf-8")
    compose = yaml.safe_load((_ROOT / "tests/systemlab/compose.yml").read_text(encoding="utf-8"))

    assert "docker-ce" not in base_image
    assert "/var/lib/docker" not in base_image
    for service in compose["services"].values():
        assert not any("/var/lib/docker" in str(volume) for volume in service.get("volumes", []))


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
    assert "docker pull --quiet ubuntu:24.04" in preflight
    assert "# syntax=docker/dockerfile:1" not in base_image
    assert "# syntax=docker/dockerfile:1" not in controller_image
    assert "getent ahosts" in preflight
    assert "registry-1.docker.io" in preflight
    assert "colima start meridian --dns 192.168.5.2 --dns 1.1.1.1" in preflight
    assert 'colima ssh -p "$profile"' in preflight
    assert "rm -f /etc/resolv.conf" in preflight
    assert "ARG TARGETARCH" in base_image
    assert 'if [ "$TARGETARCH" = "amd64" ]' in base_image
    assert "APT::Update::Error-Mode" in base_image
    assert "APT::Update::Error-Mode" in controller_image


def test_systemlab_preflight_exercises_daemon_registry_before_container_dns(tmp_path: Path) -> None:
    result, docker_calls = _run_fake_systemlab_preflight(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "daemon registry access, and container DNS" in result.stdout
    pull_call = next(index for index, call in enumerate(docker_calls) if call.startswith("pull "))
    run_call = next(index for index, call in enumerate(docker_calls) if call.startswith("run "))
    assert pull_call < run_call


def test_systemlab_preflight_reports_daemon_dns_before_container_probe(tmp_path: Path) -> None:
    result, docker_calls = _run_fake_systemlab_preflight(tmp_path, failure="daemon")

    assert result.returncode == 2
    assert "Docker daemon cannot reach Docker Hub" in result.stderr
    assert not any(call.startswith("run ") for call in docker_calls)


def test_systemlab_preflight_distinguishes_container_dns_failure(tmp_path: Path) -> None:
    result, docker_calls = _run_fake_systemlab_preflight(tmp_path, failure="container")

    assert result.returncode == 2
    assert "Docker containers cannot resolve the package mirrors" in result.stderr
    assert any(call.startswith("pull ") for call in docker_calls)
    assert any(call.startswith("run ") for call in docker_calls)


@pytest.mark.parametrize("name", sorted(orchestrator.SUPPORTED_TOPOLOGIES))
def test_realvm_manifest_matches_implemented_checks(name: str) -> None:
    topology_path = _ROOT / f"tests/realvm/topologies/{name}.yml"
    verify_path = _ROOT / "tests/realvm/verify" / orchestrator.VERIFIERS[name]
    topology = yaml.safe_load(topology_path.read_text(encoding="utf-8"))
    assert set(topology["verify"]) == {"tier_alpha"}
    declared = tuple(topology["verify"]["tier_alpha"])
    implemented = tuple(re.findall(r"^# VERIFY: ([a-z0-9_]+)$", verify_path.read_text(encoding="utf-8"), re.MULTILINE))

    assert declared == implemented
    assert declared == orchestrator.SUPPORTED_TOPOLOGIES[name]
    assert "reality_xray_handshake" not in declared
    assert "bogus_uuid_rejected" not in declared
    orchestrator.require_supported_topology(orchestrator.load_topology(name))


@pytest.mark.parametrize("name", sorted(orchestrator.SUPPORTED_TOPOLOGIES))
def test_realvm_verifier_is_valid_bash(name: str) -> None:
    verify_path = _ROOT / "tests/realvm/verify" / orchestrator.VERIFIERS[name]

    subprocess.run(["bash", "-n", str(verify_path)], check=True)


def test_realvm_domain_manifest_drift_is_refused_before_provider_access() -> None:
    topology = orchestrator.load_topology("domain")
    topology.verify = {"tier_alpha": ["panel_certificate_names_domain"]}

    with pytest.raises(SystemExit) as exc_info:
        orchestrator.require_supported_topology(topology)

    assert exc_info.value.code == 2


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


def test_realvm_refuses_a_checkout_without_an_agent_build_before_provider_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from meridian.core.errors import LocalStateError

    def no_agent() -> None:
        raise LocalStateError("The node agent binary is missing.", hint="Run `make agent`.")

    def unexpected_provider_access(_name: str) -> None:
        raise AssertionError("the provider was reached")

    monkeypatch.setattr("meridian.control_artifacts.require_agent_build", no_agent)
    monkeypatch.setattr(orchestrator, "make_provider", unexpected_provider_access)

    with pytest.raises(SystemExit) as exc_info:
        orchestrator.cmd_up(Namespace(topology="domain", keep=False))

    assert exc_info.value.code == 2


@pytest.mark.parametrize("missing", ["mapping", "script"])
def test_realvm_refuses_an_unwired_verifier_before_provider_access(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if missing == "mapping":
        monkeypatch.delitem(orchestrator.VERIFIERS, "single")
    else:
        monkeypatch.setitem(orchestrator.VERIFIERS, "single", "missing.sh")
    monkeypatch.setattr(orchestrator, "require_agent_build", lambda: None)
    monkeypatch.setattr(orchestrator, "make_provider", lambda _name: pytest.fail("the provider was reached"))

    with pytest.raises(SystemExit) as exc_info:
        orchestrator.cmd_up(Namespace(topology="single", keep=False))

    assert exc_info.value.code == 2


def test_realvm_refuses_ci_before_provider_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(orchestrator, "require_agent_build", lambda: None)
    monkeypatch.setattr(orchestrator, "make_provider", lambda _name: pytest.fail("the provider was reached"))

    with pytest.raises(SystemExit) as exc_info:
        orchestrator.cmd_up(Namespace(topology="single", keep=False))

    assert exc_info.value.code == 2


def test_realvm_verification_isolates_home_outside_checkout_and_redacts_streamed_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    topology = orchestrator.load_topology("single")
    record = tmp_path / "home-path"
    script = tmp_path / "verify.sh"
    script.write_text(
        f'printf "%s" "$MERIDIAN_HOME" > {shlex.quote(str(record))}\n'
        'echo "https://198.51.100.10/sub/opaque-token $HCLOUD_TOKEN"\nexit 75\n'
    )
    monkeypatch.setitem(orchestrator.VERIFIERS, "single", str(script))
    monkeypatch.setattr(orchestrator, "__file__", str(tmp_path / "orchestrator.py"))
    monkeypatch.setenv("HCLOUD_TOKEN", "provider-token")
    instance = VMInstance(
        id="vm-1",
        name="test-exit",
        status="running",
        public_ipv4="198.51.100.10",
        public_ipv6=None,
        region="nbg1",
        labels={orchestrator.FLEET_ID_LABEL: "test-fleet"},
        provider="stub",
    )

    assert orchestrator.run_verification(topology, [instance]) == 75
    home = Path(record.read_text())
    assert not home.is_relative_to(tmp_path)
    assert not home.exists()
    output = capsys.readouterr().out
    assert "198.51.100.10" not in output
    assert "opaque-token" not in output
    assert "provider-token" not in output


def test_realvm_keep_retains_a_private_fleet_id_for_usable_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    topology = orchestrator.load_topology("single")
    record = tmp_path / "home-path"
    script = tmp_path / "verify.sh"
    script.write_text(f'printf "%s" "$MERIDIAN_HOME" > {shlex.quote(str(record))}\nexit 0\n')
    monkeypatch.setitem(orchestrator.VERIFIERS, "single", str(script))
    fleet_id = "01234567-89ab-cdef-0123-456789abcdef"
    instance = VMInstance(
        id="vm-1",
        name="test-exit",
        status="running",
        public_ipv4="198.51.100.10",
        public_ipv6=None,
        region="nbg1",
        labels={orchestrator.FLEET_ID_LABEL: fleet_id},
        provider="stub",
    )
    try:
        assert orchestrator.run_verification(topology, [instance], keep=True) == 0
        home = Path(record.read_text())
        assert home.exists()
        assert (home.parent / "fleet-id").read_text().strip() == fleet_id
        assert "$(cat " in capsys.readouterr().out
    finally:
        if record.exists():
            import shutil

            shutil.rmtree(Path(record.read_text()).parent)


@pytest.mark.parametrize("verdict", [0, 1, 75, "crash"])
def test_upgrade_tears_down_in_finally_on_every_verifier_outcome(
    verdict: int | str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.realvm import upgrade_verifier

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(orchestrator, "require_agent_build", lambda: None)
    monkeypatch.setattr(upgrade_verifier, "preflight_upgrade", lambda _root, _env: ("4.0.0b10", "0.1.0"))
    monkeypatch.setattr(orchestrator, "make_provider", lambda _name: object())
    monkeypatch.setattr(orchestrator, "confirm_cost", lambda *args, **kwargs: None)
    monkeypatch.setattr(orchestrator, "provision", lambda *args: [])
    destroyed: list[str] = []
    monkeypatch.setattr(orchestrator, "destroy_fleet", lambda _provider, fleet_id: destroyed.append(fleet_id))

    def verify(_topology: object, _instances: object, **kwargs: object) -> int:
        assert kwargs["upgrade_from"] == "4.0.0b10"
        if verdict == "crash":
            raise RuntimeError("verifier crashed")
        return int(verdict)

    monkeypatch.setattr(orchestrator, "run_verification", verify)
    if verdict == "crash":
        with pytest.raises(RuntimeError, match="verifier crashed"):
            orchestrator.cmd_up(Namespace(topology="upgrade", keep=False))
    else:
        assert orchestrator.cmd_up(Namespace(topology="upgrade", keep=False)) == verdict
    assert len(destroyed) == 1


def test_upgrade_uses_a_fresh_ssh_key_and_removes_it_after_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.realvm import upgrade_verifier

    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("MERIDIAN_TEST_SSH_KEY", raising=False)
    monkeypatch.setattr(orchestrator, "require_agent_build", lambda: None)
    monkeypatch.setattr(upgrade_verifier, "preflight_upgrade", lambda _root, _env: ("4.0.0b10", "0.1.0"))
    monkeypatch.setattr(orchestrator, "make_provider", lambda _name: object())
    monkeypatch.setattr(orchestrator, "confirm_cost", lambda *args, **kwargs: None)
    observed: list[Path] = []

    def keygen(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        private = Path(args[-1])
        private.write_text("private-test-key")
        private.with_suffix(".pub").write_text("ssh-ed25519 dummy-key")
        return subprocess.CompletedProcess(args, 0, "", "")

    def provision(*args: object) -> list[VMInstance]:
        key = os.environ.get("MERIDIAN_TEST_SSH_KEY", "")
        assert key, "the upgrade run did not create its own SSH key"
        observed.append(Path(key))
        assert observed[-1].exists()
        return []

    monkeypatch.setattr(orchestrator.subprocess, "run", keygen)
    monkeypatch.setattr(orchestrator, "provision", provision)
    monkeypatch.setattr(orchestrator, "run_verification", lambda *args, **kwargs: 0)
    monkeypatch.setattr(orchestrator, "destroy_fleet", lambda *args: None)
    assert orchestrator.cmd_up(Namespace(topology="upgrade", keep=False)) == 0
    assert len(observed) == 1 and not observed[0].exists()
    assert "MERIDIAN_TEST_SSH_KEY" not in os.environ
