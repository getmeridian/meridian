"""Focused server-side resource driver contracts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler.models import (
    CertificatePayload,
    DeploymentContract,
    DeploymentTarget,
    FirewallRulePayload,
    NginxArtifactPayload,
    NginxRouteSpec,
    NodeBindingPayload,
    NodeRuntimePayload,
    ResourcePlan,
    ServerBaselinePayload,
    make_resource,
)
from meridian.config import REMNAWAVE_NODE_IMAGE
from meridian.provision.runtime_migration import LEGACY_NODE_PROBE
from meridian.provision.steps import StepResult
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceReconcileError,
    UnknownResourceOutcome,
    build_resource_actions,
    observation_converges,
)
from meridian.reconciler.server_drivers import (
    CertificateDriver,
    FirewallRuleDriver,
    NginxArtifactDriver,
    NodeRuntimeDriver,
    ServerBaselineDriver,
    ServerDriverContext,
)
from meridian.reconciler.server_evidence import baseline_attestation
from meridian.reconciler.server_render import artifact_token, nginx_artifact_path, render_nginx_artifact
from meridian.ssh import ServerConnection
from tests.support.mock_connection import MockConnection
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
        panel=fields.pop("panel", MagicMock()),
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


def _node_runtime_case(*, warp: bool = False) -> tuple[NodeRuntimeDriver, ResourceAction, MagicMock]:
    binding = make_resource(
        "binding:exit-a",
        NodeBindingPayload(
            workload_ref="exit-a",
            server_ref="srv-exit",
            name="Meridian v4 exit-a",
            profile_ref="profile:exit-a",
            inbound_refs=["inbound:exit-a"],
        ),
    )
    runtime = make_resource(
        "node:exit-a",
        NodeRuntimePayload(
            workload_ref="exit-a",
            server_ref="srv-exit",
            binding_ref=binding.logical_id,
            warp=warp,
        ),
        dependencies=[binding.logical_id],
    )
    plan = resource_plan(binding, runtime)
    action = build_resource_actions(plan, 1)[1]
    cluster = ClusterConfig(
        managed_bindings={
            f"{binding.logical_id}@1": ManagedResourceBinding(
                logical_id=binding.logical_id,
                resource_kind="node_binding",
                generation=1,
                remote_id="node-uuid",
            )
        }
    )
    panel = MagicMock()
    connection = _connection()
    connection.run.return_value = _result()
    driver = NodeRuntimeDriver(
        _context(plan, connection, cluster=cluster, panel=panel, node_secrets={"exit-a": "node-secret"})
    )
    return driver, action, panel


def _observed_node_runtime(
    *, compose: str = "compose\n", warp: bool = False
) -> tuple[NodeRuntimeDriver, ResourceAction, MagicMock]:
    """Node runtime driver whose server holds `compose` and whose panel Node is connected."""
    driver, action, panel = _node_runtime_case(warp=warp)
    connection = _connection()
    connection.get_text.return_value = _result(stdout=compose)
    connection.run.return_value = _result(stdout="true\n")
    driver.context.connection_for = lambda _ref: connection
    panel.get_node.return_value = SimpleNamespace(is_connected=True)
    return driver, action, connection


def test_node_runtime_missing_container_is_confirmed_drift() -> None:
    driver, action, connection = _observed_node_runtime()
    connection.run.side_effect = [_result(returncode=1, stdout="Error: No such object: netagent\n"), _result()]

    assert not observation_converges(action, driver.observe(action, None))


@pytest.mark.parametrize("survivor", ["container", "dir"])
def test_node_runtime_surviving_pre_rename_container_or_directory_is_drift(survivor: str) -> None:
    from meridian.node_deploy import render_node_compose

    driver, action, connection = _observed_node_runtime(compose=render_node_compose(REMNAWAVE_NODE_IMAGE))
    assert observation_converges(action, driver.observe(action, None))

    connection.run.side_effect = lambda command, **_: (
        _result(stdout=f"{survivor}\n") if command == LEGACY_NODE_PROBE else _result(stdout="true\n")
    )

    assert not observation_converges(action, driver.observe(action, None))


def _legacy_node_apply(*, connected: bool) -> tuple[MockConnection, list[int], MagicMock]:
    """Apply onto a server still running the pre-rename node; returns calls, call count at deploy, and deploy."""
    driver, action, _ = _node_runtime_case()
    conn = MockConnection()
    conn.when(LEGACY_NODE_PROBE, stdout="container\ndir\n")
    conn.when("cat /opt/netagent/docker-compose.yml", rc=1, stderr="No such file or directory")
    conn.when("cat /opt/remnanode/docker-compose.yml", stdout="services:\n  x:\n    image: remnawave/node:3.9.1\n")
    driver.context.connection_for = lambda _ref: conn
    at_deploy: list[int] = []
    deploy = MagicMock(side_effect=lambda *_a, **_k: at_deploy.append(conn.call_count) or True)
    with (
        patch("meridian.reconciler.server_drivers.deploy_node_container", deploy),
        patch("meridian.reconciler.server_drivers.wait_for_node_connected", return_value=connected),
    ):
        if connected:
            driver.apply(action, None)
        else:
            with pytest.raises(ResourceReconcileError, match="did not connect"):
                driver.apply(action, None)
    return conn, at_deploy, deploy


def test_node_runtime_rename_stops_the_legacy_container_first_and_removes_it_last() -> None:
    conn, at_deploy, deploy = _legacy_node_apply(connected=True)

    calls = conn.calls
    stop = next(i for i, call in enumerate(calls) if "docker stop -t 30 remnawave-node" in call)
    remove = next(i for i, call in enumerate(calls) if "rm -rf /opt/remnanode" in call)
    # Same host ports: the legacy node releases them before the renamed one binds.
    assert stop < at_deploy[0] <= remove
    assert "docker rm -f remnawave-node" in calls[remove]
    # The operator's newer image choice carries over from the legacy compose file.
    assert deploy.call_args.kwargs["image"] == "remnawave/node:3.9.1"


def test_node_runtime_rename_that_never_connects_restarts_the_legacy_container() -> None:
    conn, _, _ = _legacy_node_apply(connected=False)

    conn.assert_called_with_pattern("docker start remnawave-node")
    conn.assert_not_called_with_pattern("rm -rf /opt/remnanode")


@pytest.mark.parametrize(
    ("returncode", "output"),
    [
        (1, "permission denied while connecting to the Docker daemon socket"),
        (124, "inspection timed out"),
        (127, "docker: command not found"),
        (255, "SSH connection closed"),
    ],
)
def test_node_runtime_rejects_unavailable_docker_evidence(returncode: int, output: str) -> None:
    driver, action, connection = _observed_node_runtime()
    connection.run.return_value = _result(returncode=returncode, stderr=output)

    with pytest.raises(ResourceReconcileError, match="Docker container netagent evidence is unavailable"):
        driver.observe(action, None)


def test_node_runtime_missing_managed_warp_binary_is_drift() -> None:
    driver, action, connection = _observed_node_runtime(warp=True)
    connection.run.side_effect = [
        _result(stdout="true\n"),
        _result(),
        _result(returncode=127, stderr="warp-cli: command not found"),
    ]

    assert not observation_converges(action, driver.observe(action, None))


def test_node_runtime_rejects_warp_daemon_failure() -> None:
    driver, action, connection = _observed_node_runtime(warp=True)
    connection.run.side_effect = [
        _result(stdout="true\n"),
        _result(),
        _result(returncode=1, stderr="Cannot connect to daemon"),
    ]

    with pytest.raises(ResourceReconcileError, match="WARP status evidence is unavailable"):
        driver.observe(action, None)


@pytest.mark.parametrize("connected", [True, False])
def test_node_runtime_apply_waits_for_bound_node_to_connect(connected: bool) -> None:
    driver, action, panel = _node_runtime_case()

    with (
        patch("meridian.reconciler.server_drivers.deploy_node_container", return_value=True),
        patch("meridian.reconciler.server_drivers.wait_for_node_connected", return_value=connected) as wait,
    ):
        if connected:
            driver.apply(action, None)
        else:
            with pytest.raises(ResourceReconcileError, match="did not connect to the panel"):
                driver.apply(action, None)

    wait.assert_called_once_with(panel, "node-uuid")


def _node_runtime_with_compose(image: str) -> tuple[NodeRuntimeDriver, ResourceAction]:
    from meridian.node_deploy import render_node_compose

    driver, action, _ = _observed_node_runtime(compose=render_node_compose(image))
    return driver, action


def _applied_image(driver: NodeRuntimeDriver, action: ResourceAction) -> str:
    with (
        patch("meridian.reconciler.server_drivers.deploy_node_container", return_value=True) as deploy,
        patch("meridian.reconciler.server_drivers.wait_for_node_connected", return_value=True),
    ):
        driver.apply(action, None)
    return deploy.call_args.kwargs["image"]


@pytest.mark.parametrize("image", ["remnawave/node:3.9.1", "remnawave/node:4.0.0", "remnawave/node:latest"])
def test_node_runtime_accepts_operator_upgraded_image(image: str) -> None:
    driver, action = _node_runtime_with_compose(image)

    assert observation_converges(action, driver.observe(action, None))
    assert _applied_image(driver, action) == image


def test_node_runtime_upgrades_image_older_than_the_pin() -> None:
    from meridian.config import REMNAWAVE_NODE_IMAGE

    driver, action = _node_runtime_with_compose("remnawave/node:2.8.0")

    assert not observation_converges(action, driver.observe(action, None))
    assert _applied_image(driver, action) == REMNAWAVE_NODE_IMAGE


def test_node_runtime_adopts_the_pinned_digest_for_a_tag_only_compose_once() -> None:
    from meridian.config import REMNAWAVE_NODE_IMAGE
    from meridian.remnawave.versions import parse_image

    driver, action = _node_runtime_with_compose(f"remnawave/node:{parse_image(REMNAWAVE_NODE_IMAGE).tag}")
    assert not observation_converges(action, driver.observe(action, None))

    pinned_driver, pinned_action = _node_runtime_with_compose(REMNAWAVE_NODE_IMAGE)
    assert observation_converges(pinned_action, pinned_driver.observe(pinned_action, None))


def test_node_runtime_accepts_another_spelling_of_the_pinned_digest() -> None:
    from meridian.config import REMNAWAVE_NODE_IMAGE
    from meridian.remnawave.versions import parse_image

    pin = parse_image(REMNAWAVE_NODE_IMAGE)
    driver, action = _node_runtime_with_compose(f"docker.io/{pin.repository}@{pin.digest}")

    assert observation_converges(action, driver.observe(action, None))
