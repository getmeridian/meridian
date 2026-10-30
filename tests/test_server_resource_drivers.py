"""Focused server-side resource driver contracts."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from meridian.cluster import ClusterConfig
from meridian.compiler.models import (
    CertificatePayload,
    DeploymentContract,
    DeploymentTarget,
    FirewallRulePayload,
    NginxArtifactPayload,
    NginxRouteSpec,
    ResourcePlan,
    ServerBaselinePayload,
    make_resource,
)
from meridian.provision.steps import StepResult
from meridian.reconciler import render_contract
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceReconcileError,
    UnknownResourceOutcome,
    build_resource_actions,
    observation_converges,
    observation_shortfall,
)
from meridian.reconciler.server_drivers import (
    CertificateDriver,
    FirewallRuleDriver,
    NginxArtifactDriver,
    ServerBaselineDriver,
    ServerDriverContext,
)
from meridian.reconciler.server_evidence import baseline_attestation, content_mismatch, listener_wait_command
from meridian.reconciler.server_render import artifact_token, nginx_artifact_path, render_nginx_artifact
from meridian.ssh import ServerConnection
from tests.support.topology import resource_plan

_LISTENING = "LISTEN 0 1024 0.0.0.0:8443 0.0.0.0:*\n"


def _result(*, returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _single_action(logical_id: str, payload):
    resource = make_resource(logical_id, payload)
    plan = resource_plan(resource)
    return plan, build_resource_actions(plan, 1)[0]


def _connection() -> MagicMock:
    connection = MagicMock(spec=ServerConnection)
    connection.user = "root"
    return connection


def _context(plan: ResourcePlan, connection: MagicMock, **fields) -> ServerDriverContext:
    return ServerDriverContext(
        plan=plan,
        cluster=fields.pop("cluster", ClusterConfig()),
        connection_for=lambda _ref: connection,
        server_addresses={"srv-exit": "198.51.100.10"},
        **fields,
    )


def _action(*, install_docker: bool = True):
    return _single_action(
        "baseline:server", ServerBaselinePayload(server_ref="srv-exit", install_docker=install_docker)
    )


def _baseline_case(*, install_docker: bool = True) -> tuple[ServerBaselineDriver, ResourceAction, MagicMock, str]:
    """Baseline driver, its action, the server connection, and the attestation file content that matches."""
    plan, action = _action(install_docker=install_docker)
    connection = _connection()
    attestation = f"{baseline_attestation(action, plan, 'srv-exit')}\n"
    return ServerBaselineDriver(_context(plan, connection)), action, connection, attestation


def test_baseline_attestation_ignores_other_servers_joining_or_leaving() -> None:
    plan, action = _action()
    own = DeploymentTarget(host="198.51.100.10", user="root", port=22)

    def attest(**targets: DeploymentTarget) -> str:
        contract = DeploymentContract(
            server_targets=targets,
            runtime_pins={"remnawave_node_image": "remnawave/node:3.4.2"},
            renderer_contract="test-v1",
        )
        return baseline_attestation(action, plan.model_copy(update={"deployment_contract": contract}), "srv-exit")

    relay = DeploymentTarget(host="198.51.100.20", user="root", port=22)
    assert attest(**{"srv-exit": own}) == attest(**{"srv-exit": own, "srv-relay": relay})
    assert attest(**{"srv-exit": own}) != attest(**{"srv-exit": own.model_copy(update={"port": 2222})})


def test_baseline_attestation_ignores_releases_and_other_renderers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new CLI version or another renderer's bump once re-ran every server's whole baseline."""
    plan, action = _action()
    own = DeploymentTarget(host="198.51.100.10", user="root", port=22)

    def attest(version: str, digest: str) -> str:
        contract = DeploymentContract(
            server_targets={"srv-exit": own}, runtime_pins={"meridian_version": version}, renderer_contract=digest
        )
        return baseline_attestation(action, plan.model_copy(update={"deployment_contract": contract}), "srv-exit")

    before = attest("4.0.0", "test-v1")
    assert attest("4.0.1", "test-v2") == before
    monkeypatch.setitem(render_contract._RENDERER_MANIFEST, "server-baseline", "next")
    assert attest("4.0.0", "test-v1") != before


def test_server_baseline_observes_every_durable_check_and_attestation() -> None:
    driver, action, connection, attestation = _baseline_case()
    connection.run.return_value = _result()
    connection.get_text.return_value = _result(stdout=attestation)

    assert observation_converges(action, driver.observe(action, None))
    commands = [call.args[0] for call in connection.run.call_args_list]
    assert any("dpkg-query" in command for command in commands)
    assert any("sshd -T" in command for command in commands)
    assert any("systemctl is-active --quiet fail2ban" in command for command in commands)
    assert any("docker compose version" in command for command in commands)


@pytest.mark.parametrize(
    ("failing_check", "returncode"),
    [("docker info", 1), ("systemctl is-active --quiet fail2ban", 3)],
    ids=["docker-missing", "fail2ban-inactive"],
)
def test_server_baseline_negative_check_is_drift(failing_check: str, returncode: int) -> None:
    driver, action, connection, attestation = _baseline_case()
    connection.run.side_effect = lambda command, **_kwargs: _result(
        returncode=returncode if failing_check in command else 0
    )
    connection.get_text.return_value = _result(stdout=attestation)

    assert not observation_converges(action, driver.observe(action, None))


@pytest.mark.parametrize(
    ("returncode", "stderr", "match"),
    [
        *[(rc, "inspection unavailable", "evidence is unavailable") for rc in (124, 127, 255)],
        (1, "permission denied", "inspection was denied"),
    ],
    ids=["124", "127", "255", "permission-denied"],
)
def test_server_baseline_rejects_unavailable_check_evidence(returncode: int, stderr: str, match: str) -> None:
    driver, action, connection, _ = _baseline_case()
    connection.run.return_value = _result(returncode=returncode, stderr=stderr)

    with pytest.raises(ResourceReconcileError, match=match) as exc_info:
        driver.observe(action, None)

    # Only a stalled or dropped connection is worth retrying; a missing tool stays missing.
    assert exc_info.value.retryable is (returncode in {124, 255})


def test_server_baseline_missing_attestation_is_confirmed_absent() -> None:
    driver, action, connection, _ = _baseline_case()
    connection.run.return_value = _result()
    connection.get_text.return_value = _result(
        returncode=1,
        stderr="cat: /var/lib/meridian/baselines/missing: No such file or directory",
    )

    assert driver.observe(action, None).exists is False


def test_server_baseline_preserves_unknown_outcome_on_step_timeout(monkeypatch) -> None:
    driver, action, _, _ = _baseline_case()
    monkeypatch.setattr(
        "meridian.provision.Provisioner.run",
        lambda *_args, **_kwargs: [
            StepResult(
                name="Install system packages",
                status="failed",
                detail="apt command timed out after 120s",
            )
        ],
    )

    with pytest.raises(UnknownResourceOutcome, match="observation is required"):
        driver.apply(action, None)


def test_server_baseline_apply_moves_its_attestation_to_the_neutral_path() -> None:
    from meridian.reconciler.server_render import baseline_marker_path, legacy_baseline_marker_path

    driver, action, connection, _ = _baseline_case(install_docker=False)
    connection.run.return_value = _result()
    written: list[str] = []

    with (
        patch("meridian.provision.Provisioner.run", return_value=[]),
        patch(
            "meridian.reconciler.server_drivers.ensure_file_content",
            side_effect=lambda _conn, path, *_a, **_kw: written.append(path) or SimpleNamespace(ok=True),
        ),
    ):
        driver.apply(action, None)

    assert written == [baseline_marker_path(action.resource.logical_id)]
    assert "meridian" not in written[0]
    commands = [call.args[0] for call in connection.run.call_args_list]
    legacy = legacy_baseline_marker_path(action.resource.logical_id)
    assert any(command.startswith(f"rm -f {legacy}") for command in commands)


def _firewall_case() -> tuple[FirewallRuleDriver, ResourceAction, MagicMock, str]:
    """Firewall driver for tcp/443, its action, the connection, and the rule's artifact token."""
    plan, action = _single_action(
        "firewall:srv-exit:tcp:443",
        FirewallRulePayload(server_ref="srv-exit", transport="tcp", port=443),
    )
    connection = _connection()
    return (
        FirewallRuleDriver(_context(plan, connection)),
        action,
        connection,
        artifact_token(action.resource.logical_id),
    )


def test_firewall_observation_accepts_ufw_quoted_comment() -> None:
    driver, action, connection, token = _firewall_case()
    connection.run.return_value = _result(
        stdout=f"Added user rules (see 'ufw status' for running firewall):\nufw allow 443/tcp comment 'fw-{token}'\n"
    )

    assert observation_converges(action, driver.observe(action, None))


@pytest.mark.parametrize("returncode", [1, 124, 127, 255])
def test_firewall_observation_rejects_failed_ufw_inspection(returncode: int) -> None:
    driver, action, connection, _ = _firewall_case()
    connection.run.return_value = _result(returncode=returncode, stderr="ufw inspection failed")

    with pytest.raises(ResourceReconcileError, match="UFW rule inspection evidence is unavailable"):
        driver.observe(action, None)


def test_firewall_successfully_observed_missing_rule_is_drift() -> None:
    driver, action, connection, _ = _firewall_case()
    connection.run.return_value = _result(stdout="Added user rules:\n")

    assert driver.observe(action, None).exists is False


def test_firewall_rule_under_its_pre_rename_comment_is_drift() -> None:
    driver, action, connection, token = _firewall_case()
    connection.run.return_value = _result(
        stdout=f"ufw allow 443/tcp comment 'fw-{token}'\nufw allow 443/tcp comment 'meridian-v4-{token}'\n"
    )

    assert not observation_converges(action, driver.observe(action, None))


def test_firewall_migration_deletes_the_legacy_comment_then_re_adds_the_renamed_rule() -> None:
    driver, action, connection, token = _firewall_case()
    connection.run.side_effect = lambda command, **_kw: _result(
        stdout=f"ufw allow 443/tcp comment 'meridian-v4-{token}'\n" if command.startswith("ufw show added") else ""
    )

    receipt = driver.apply(action, None)

    commands = [call.args[0] for call in connection.run.call_args_list]
    assert receipt.remote_id == f"fw-{token}"
    delete = next(index for index, command in enumerate(commands) if f"# meridian-v4-{token}$" in command)
    adds = [index for index, command in enumerate(commands) if command == f"ufw allow 443/tcp comment fw-{token}"]
    assert adds[0] < delete < adds[-1]


def test_failed_firewall_migration_restores_the_legacy_rule() -> None:
    driver, action, connection, token = _firewall_case()
    adds = 0

    def run(command: str, **_kw: object):
        nonlocal adds
        if command.startswith("ufw show added"):
            return _result(stdout=f"ufw allow 443/tcp comment 'meridian-v4-{token}'\n")
        if command.startswith("ufw allow") and f"fw-{token}" in command and "meridian" not in command:
            adds += 1
            return _result(returncode=1 if adds > 1 else 0, stderr="ERROR: rule failed")
        return _result()

    connection.run.side_effect = run

    with pytest.raises(ResourceReconcileError, match="Could not apply firewall rule"):
        driver.apply(action, None)

    commands = [call.args[0] for call in connection.run.call_args_list]
    assert commands[-1] == f"ufw allow 443/tcp comment meridian-v4-{token}"


def test_fresh_firewall_rule_never_deletes_by_comment() -> None:
    driver, action, connection, _ = _firewall_case()
    connection.run.return_value = _result()

    driver.apply(action, None)

    assert not any("ufw --force delete" in call.args[0] for call in connection.run.call_args_list)


def _certificate_case() -> tuple[CertificateDriver, ResourceAction, MagicMock]:
    plan, action = _single_action(
        "certificate:srv-exit:example",
        CertificatePayload(server_ref="srv-exit", hostname="vpn.example.test"),
    )
    connection = _connection()
    return CertificateDriver(_context(plan, connection)), action, connection


def test_certificate_missing_file_is_confirmed_absent() -> None:
    driver, action, connection = _certificate_case()
    connection.get_text.return_value = _result(returncode=1, stderr="cat: certificate: No such file or directory")

    assert driver.observe(action, None).exists is False
    connection.run.assert_not_called()


def test_certificate_unreadable_file_is_unavailable_evidence() -> None:
    driver, action, connection = _certificate_case()
    connection.get_text.return_value = _result(returncode=1, stderr="cat: certificate: Permission denied")

    with pytest.raises(ResourceReconcileError, match="certificate file evidence is unavailable"):
        driver.observe(action, None)


def test_certificate_expiry_is_drift_after_readable_file_was_proven() -> None:
    driver, action, connection = _certificate_case()
    connection.get_text.return_value = _result(stdout="certificate bytes")
    connection.run.return_value = _result(returncode=1, stdout="Certificate will expire")

    observation = driver.observe(action, None)

    assert observation.exists is True
    assert not observation_converges(action, observation)


@pytest.mark.parametrize(
    ("names", "converges"),
    [
        ("DNS:vpn.example.test", True),
        ("DNS:www.example.test, DNS:vpn.example.test", True),
        ("DNS:vpn.example.test.old", False),
        ("DNS:old-vpn.example.test", False),
    ],
)
def test_certificate_must_name_the_host_as_a_whole_entry(names: str, converges: bool) -> None:
    driver, action, connection = _certificate_case()
    connection.get_text.return_value = _result(stdout="certificate bytes")
    stdout = f"Certificate will not expire\nX509v3 Subject Alternative Name: \n    {names}\n"
    connection.run.return_value = _result(stdout=stdout)

    assert observation_converges(action, driver.observe(action, None)) is converges


@pytest.mark.parametrize("returncode", [3, 4, 124, 127, 255])
def test_certificate_rejects_unavailable_openssl_evidence(returncode: int) -> None:
    driver, action, connection = _certificate_case()
    connection.get_text.return_value = _result(stdout="certificate bytes")
    connection.run.return_value = _result(returncode=returncode, stderr="openssl unavailable")

    with pytest.raises(ResourceReconcileError, match="certificate validation evidence is unavailable"):
        driver.observe(action, None)


def _nginx_observation_case() -> tuple[NginxArtifactDriver, ResourceAction, MagicMock]:
    payload = NginxArtifactPayload(
        server_ref="srv-exit",
        listener_port=8443,
        layer="stream",
        routes=[
            NginxRouteSpec(
                match="sni",
                server_names=["vpn.example.test"],
                backend_server_ref="srv-exit",
                backend_port=3010,
            )
        ],
    )
    plan, action = _single_action("nginx:srv-exit:stream:8443", payload)
    connection = _connection()
    connection.get_text.return_value = _result(
        stdout=render_nginx_artifact(action.resource.logical_id, payload, {"srv-exit": "198.51.100.10"})
    )

    def run(command: str, **_kwargs):
        if command.startswith("systemctl is-active"):
            return _result(stdout="active\n")
        if command.startswith("ss -H -lnt"):
            return _result(stdout=_LISTENING)
        raise AssertionError(command)

    connection.run.side_effect = run
    return NginxArtifactDriver(_context(plan, connection)), action, connection


def test_nginx_missing_artifact_is_confirmed_absent() -> None:
    driver, action, connection = _nginx_observation_case()
    connection.get_text.return_value = _result(returncode=1, stderr="cat: artifact: No such file or directory")

    assert driver.observe(action, None).exists is False


def test_nginx_unreadable_artifact_is_unavailable_evidence() -> None:
    driver, action, connection = _nginx_observation_case()
    connection.get_text.return_value = _result(returncode=1, stderr="cat: artifact: Permission denied")

    with pytest.raises(ResourceReconcileError, match="nginx artifact evidence is unavailable"):
        driver.observe(action, None)


def test_nginx_inactive_service_is_drift() -> None:
    driver, action, connection = _nginx_observation_case()
    connection.run.side_effect = [_result(returncode=3, stdout="inactive\n"), _result(stdout=_LISTENING)]

    assert not observation_converges(action, driver.observe(action, None))


@pytest.mark.parametrize(
    ("service", "listener", "match"),
    [
        (_result(returncode=1, stderr="Failed to connect to bus"), _result(stdout=_LISTENING), "nginx service"),
        (
            _result(stdout="active\n"),
            _result(returncode=127, stderr="ss: command not found"),
            "TCP listener inspection",
        ),
    ],
    ids=["systemd-daemon", "listener-inspection"],
)
def test_nginx_rejects_unavailable_runtime_evidence(service, listener, match: str) -> None:
    driver, action, connection = _nginx_observation_case()
    connection.run.side_effect = [service, listener]

    with pytest.raises(ResourceReconcileError, match=f"{match} evidence is unavailable"):
        driver.observe(action, None)


@pytest.mark.parametrize(
    ("artifact", "service", "listener", "reason"),
    [
        ("edited", "active\n", _LISTENING, "differs from the reviewed render from line 1"),
        ("rendered", "inactive\n", _LISTENING, "nginx is not active"),
        ("rendered", "active\n", "", "nothing listens on TCP port 8443"),
    ],
    ids=["edited-file", "inactive", "not-listening"],
)
def test_nginx_failure_names_the_predicate_that_failed(artifact: str, service: str, listener: str, reason: str) -> None:
    driver, action, connection = _nginx_observation_case()
    if artifact == "edited":
        connection.get_text.return_value = _result(stdout="# edited by hand\n")
    connection.run.side_effect = [
        _result(returncode=0 if service == "active\n" else 3, stdout=service),
        _result(stdout=listener),
    ]

    shortfall = observation_shortfall(action, driver.observe(action, None))

    assert reason in shortfall
    assert "vpn.example.test" not in shortfall  # file contents never reach the error


def test_listener_wait_returns_once_the_port_listens(tmp_path) -> None:
    fake_ss = tmp_path / "ss"
    fake_ss.write_text("#!/bin/sh\necho 'LISTEN 0 511 127.0.0.1:18565 0.0.0.0:*'\n")
    fake_ss.chmod(0o755)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin"}

    listening = subprocess.run(["sh", "-c", listener_wait_command(18565, seconds=1)], env=env, check=False)
    absent = subprocess.run(["sh", "-c", listener_wait_command(1856, seconds=1)], env=env, check=False)

    assert (listening.returncode, absent.returncode) == (0, 1)


def test_port_443_stream_apply_retires_the_legacy_artifact(monkeypatch) -> None:
    plan, action = _single_action(
        "nginx:shared:443:stream",
        NginxArtifactPayload(
            server_ref="srv-exit",
            listener_port=443,
            layer="stream",
            routes=[
                NginxRouteSpec(
                    match="sni",
                    server_names=[""],
                    backend_server_ref="srv-exit",
                    backend_port=8443,
                )
            ],
        ),
    )
    connection = _connection()
    artifact_path = nginx_artifact_path(action.resource.logical_id, "stream")

    def get_text(path: str, *, timeout: int):
        if path == "/etc/nginx/nginx.conf":
            return _result(stdout="stream { include /etc/nginx/stream.d/*.conf; }\n")
        if path == artifact_path:
            return _result(returncode=1)
        if path == "/etc/nginx/stream.d/meridian.conf":
            return _result(stdout="legacy\n")
        raise AssertionError(path)

    connection.get_text.side_effect = get_text
    connection.run.return_value = _result()
    monkeypatch.setattr(
        "meridian.reconciler.server_drivers.InstallNginx.run",
        lambda *_args, **_kwargs: StepResult(name="Install nginx", status="ok"),
    )
    monkeypatch.setattr(
        "meridian.reconciler.server_drivers.ensure_file_content",
        lambda *_args, **_kwargs: SimpleNamespace(ok=True, result=None),
    )

    NginxArtifactDriver(_context(plan, connection)).apply(action, None)

    assert any(call.args[0] == "rm -f /etc/nginx/stream.d/meridian.conf" for call in connection.run.call_args_list)
    # Observation follows at once, so apply waits for the reloaded listener first.
    assert connection.run.call_args_list[-1].args[0] == listener_wait_command(443)


@pytest.mark.parametrize(
    ("observed", "reason"),
    [
        (None, "the file is missing"),
        ("a\nb\n", ""),
        ("a\r\nb\r\n", "only in line endings or a trailing newline"),
        ("a\nb", "only in line endings or a trailing newline"),
        ("a\n", "from line 2 (1 lines on the server, 2 reviewed)"),
        ("a\nb\nc\n", "from line 3 (3 lines on the server, 2 reviewed)"),
        ("a\nX\n", "from line 2 (2 lines on the server, 2 reviewed)"),
    ],
    ids=["missing", "same", "crlf", "no-final-newline", "shorter", "longer", "edited"],
)
def test_content_mismatch_says_where_without_quoting(observed: str | None, reason: str) -> None:
    found = content_mismatch("the file", observed, "a\nb\n")
    assert found.endswith(reason) and (found == "") == (reason == "")
