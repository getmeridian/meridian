"""Focused server-side resource driver contracts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler.models import (
    COMPILER_VERSION,
    CertificatePayload,
    FirewallRulePayload,
    NginxArtifactPayload,
    NginxRouteSpec,
    NodeBindingPayload,
    NodeRuntimePayload,
    ResourcePlan,
    ServerBaselinePayload,
    canonical_hash,
    compute_plan_hash,
    make_resource,
)
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
from meridian.reconciler.server_render import artifact_token, nginx_artifact_path, render_nginx_artifact
from meridian.ssh import ServerConnection


def _action(*, install_docker: bool = True):
    resource = make_resource(
        "baseline:server",
        ServerBaselinePayload(
            server_ref="srv-exit",
            install_docker=install_docker,
        ),
    )
    intent_hash = "1" * 64
    plan = ResourcePlan(
        intent_hash=intent_hash,
        plan_hash=compute_plan_hash(
            compiler_version=COMPILER_VERSION,
            intent_hash=intent_hash,
            resources=[resource],
        ),
        resources=[resource],
    )
    return plan, build_resource_actions(plan, 1)[0]


def _result(*, returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _single_action(logical_id: str, payload):
    resource = make_resource(logical_id, payload)
    intent_hash = "1" * 64
    plan = ResourcePlan(
        intent_hash=intent_hash,
        plan_hash=compute_plan_hash(
            compiler_version=COMPILER_VERSION,
            intent_hash=intent_hash,
            resources=[resource],
        ),
        resources=[resource],
    )
    return plan, build_resource_actions(plan, 1)[0]


def _baseline_attestation(plan: ResourcePlan, action) -> str:
    return canonical_hash(
        {
            "resource": action.expected_hash,
            "deployment_contract": plan.deployment_contract.model_dump(mode="json"),
        }
    )


def test_server_baseline_observes_every_durable_check_and_attestation() -> None:
    plan, action = _action()
    connection = MagicMock(spec=ServerConnection)
    connection.user = "root"
    connection.run.return_value = SimpleNamespace(returncode=0, stdout="")
    connection.get_text.return_value = SimpleNamespace(
        returncode=0,
        stdout=f"{_baseline_attestation(plan, action)}\n",
    )
    driver = ServerBaselineDriver(
        ServerDriverContext(
            plan=plan,
            cluster=ClusterConfig(),
            panel=MagicMock(),
            connection_for=lambda _ref: connection,
            server_addresses={"srv-exit": "198.51.100.10"},
        )
    )

    assert observation_converges(action, driver.observe(action, None))
    commands = [call.args[0] for call in connection.run.call_args_list]
    assert any("dpkg-query" in command for command in commands)
    assert any("sshd -T" in command for command in commands)
    assert any("systemctl is-active --quiet fail2ban" in command for command in commands)
    assert any("docker compose version" in command for command in commands)


def test_server_baseline_fails_closed_when_docker_is_missing() -> None:
    plan, action = _action()
    connection = MagicMock(spec=ServerConnection)
    connection.user = "root"
    connection.run.side_effect = lambda command, **_kwargs: SimpleNamespace(
        returncode=1 if "docker info" in command else 0,
        stdout="",
    )
    connection.get_text.return_value = SimpleNamespace(
        returncode=0,
        stdout=f"{_baseline_attestation(plan, action)}\n",
    )
    driver = ServerBaselineDriver(
        ServerDriverContext(
            plan=plan,
            cluster=ClusterConfig(),
            panel=MagicMock(),
            connection_for=lambda _ref: connection,
            server_addresses={"srv-exit": "198.51.100.10"},
        )
    )

    assert not observation_converges(action, driver.observe(action, None))


@pytest.mark.parametrize("returncode", [124, 127, 255])
def test_server_baseline_rejects_unavailable_check_evidence(returncode: int) -> None:
    plan, action = _action()
    connection = MagicMock(spec=ServerConnection)
    connection.user = "root"
    connection.run.return_value = _result(returncode=returncode, stderr="inspection unavailable")
    driver = ServerBaselineDriver(
        ServerDriverContext(
            plan=plan,
            cluster=ClusterConfig(),
            panel=MagicMock(),
            connection_for=lambda _ref: connection,
            server_addresses={"srv-exit": "198.51.100.10"},
        )
    )

    with pytest.raises(ResourceReconcileError, match="evidence is unavailable") as exc_info:
        driver.observe(action, None)

    assert exc_info.value.retryable is True


def test_server_baseline_rejects_permission_failure_instead_of_reporting_drift() -> None:
    plan, action = _action()
    connection = MagicMock(spec=ServerConnection)
    connection.user = "root"
    connection.run.return_value = _result(returncode=1, stderr="permission denied")
    driver = ServerBaselineDriver(
        ServerDriverContext(
            plan=plan,
            cluster=ClusterConfig(),
            panel=MagicMock(),
            connection_for=lambda _ref: connection,
            server_addresses={"srv-exit": "198.51.100.10"},
        )
    )

    with pytest.raises(ResourceReconcileError, match="inspection was denied"):
        driver.observe(action, None)


def test_server_baseline_accepts_inactive_fail2ban_as_negative_state() -> None:
    plan, action = _action()
    connection = MagicMock(spec=ServerConnection)
    connection.user = "root"

    def run(command: str, **_kwargs):
        if "systemctl is-active --quiet fail2ban" in command:
            return _result(returncode=3)
        return _result()

    connection.run.side_effect = run
    connection.get_text.return_value = _result(stdout=f"{_baseline_attestation(plan, action)}\n")
    driver = ServerBaselineDriver(
        ServerDriverContext(
            plan=plan,
            cluster=ClusterConfig(),
            panel=MagicMock(),
            connection_for=lambda _ref: connection,
            server_addresses={"srv-exit": "198.51.100.10"},
        )
    )

    assert not observation_converges(action, driver.observe(action, None))


def test_server_baseline_missing_attestation_is_confirmed_absent() -> None:
    plan, action = _action()
    connection = MagicMock(spec=ServerConnection)
    connection.user = "root"
    connection.run.return_value = _result()
    connection.get_text.return_value = _result(
        returncode=1,
        stderr="cat: /var/lib/meridian/baselines/missing: No such file or directory",
    )
    driver = ServerBaselineDriver(
        ServerDriverContext(
            plan=plan,
            cluster=ClusterConfig(),
            panel=MagicMock(),
            connection_for=lambda _ref: connection,
            server_addresses={"srv-exit": "198.51.100.10"},
        )
    )

    observation = driver.observe(action, None)

    assert observation.exists is False


def test_server_baseline_preserves_unknown_outcome_on_step_timeout(monkeypatch) -> None:
    plan, action = _action()
    connection = MagicMock(spec=ServerConnection)
    connection.user = "root"
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
    driver = ServerBaselineDriver(
        ServerDriverContext(
            plan=plan,
            cluster=ClusterConfig(),
            panel=MagicMock(),
            connection_for=lambda _ref: connection,
            server_addresses={"srv-exit": "198.51.100.10"},
        )
    )

    with pytest.raises(UnknownResourceOutcome, match="observation is required"):
        driver.apply(action, None)


def test_firewall_observation_accepts_ufw_quoted_comment() -> None:
    resource = make_resource(
        "firewall:srv-exit:tcp:443",
        FirewallRulePayload(
            server_ref="srv-exit",
            transport="tcp",
            port=443,
        ),
    )
    intent_hash = "1" * 64
    plan = ResourcePlan(
        intent_hash=intent_hash,
        plan_hash=compute_plan_hash(
            compiler_version=COMPILER_VERSION,
            intent_hash=intent_hash,
            resources=[resource],
        ),
        resources=[resource],
    )
    action = build_resource_actions(plan, 1)[0]
    marker = f"meridian-v4-{artifact_token(resource.logical_id)}"
    connection = MagicMock(spec=ServerConnection)
    connection.run.return_value = SimpleNamespace(
        returncode=0,
        stdout=(f"Added user rules (see 'ufw status' for running firewall):\nufw allow 443/tcp comment '{marker}'\n"),
    )
    driver = FirewallRuleDriver(
        ServerDriverContext(
            plan=plan,
            cluster=ClusterConfig(),
            panel=MagicMock(),
            connection_for=lambda _ref: connection,
            server_addresses={"srv-exit": "198.51.100.10"},
        )
    )

    assert observation_converges(action, driver.observe(action, None))


@pytest.mark.parametrize("returncode", [1, 124, 127, 255])
def test_firewall_observation_rejects_failed_ufw_inspection(returncode: int) -> None:
    plan, action = _single_action(
        "firewall:srv-exit:tcp:443",
        FirewallRulePayload(server_ref="srv-exit", transport="tcp", port=443),
    )
    connection = MagicMock(spec=ServerConnection)
    connection.run.return_value = _result(returncode=returncode, stderr="ufw inspection failed")
    driver = FirewallRuleDriver(
        ServerDriverContext(
            plan=plan,
            cluster=ClusterConfig(),
            panel=MagicMock(),
            connection_for=lambda _ref: connection,
            server_addresses={"srv-exit": "198.51.100.10"},
        )
    )

    with pytest.raises(ResourceReconcileError, match="UFW rule inspection evidence is unavailable"):
        driver.observe(action, None)


def test_firewall_successfully_observed_missing_rule_is_drift() -> None:
    plan, action = _single_action(
        "firewall:srv-exit:tcp:443",
        FirewallRulePayload(server_ref="srv-exit", transport="tcp", port=443),
    )
    connection = MagicMock(spec=ServerConnection)
    connection.run.return_value = _result(stdout="Added user rules:\n")
    driver = FirewallRuleDriver(
        ServerDriverContext(
            plan=plan,
            cluster=ClusterConfig(),
            panel=MagicMock(),
            connection_for=lambda _ref: connection,
            server_addresses={"srv-exit": "198.51.100.10"},
        )
    )

    observation = driver.observe(action, None)

    assert observation.exists is False


def _certificate_case() -> tuple[CertificateDriver, ResourceAction, MagicMock]:
    plan, action = _single_action(
        "certificate:srv-exit:example",
        CertificatePayload(server_ref="srv-exit", hostname="vpn.example.test"),
    )
    connection = MagicMock(spec=ServerConnection)
    driver = CertificateDriver(
        ServerDriverContext(
            plan=plan,
            cluster=ClusterConfig(),
            panel=MagicMock(),
            connection_for=lambda _ref: connection,
            server_addresses={"srv-exit": "198.51.100.10"},
        )
    )
    return driver, action, connection


def test_certificate_missing_file_is_confirmed_absent() -> None:
    driver, action, connection = _certificate_case()
    connection.get_text.return_value = _result(
        returncode=1,
        stderr="cat: certificate: No such file or directory",
    )

    observation = driver.observe(action, None)

    assert observation.exists is False
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
    connection = MagicMock(spec=ServerConnection)
    connection.get_text.return_value = _result(
        stdout=render_nginx_artifact(action.resource.logical_id, payload, {"srv-exit": "198.51.100.10"})
    )

    def run(command: str, **_kwargs):
        if command.startswith("systemctl is-active"):
            return _result(stdout="active\n")
        if command.startswith("ss -H -lnt"):
            return _result(stdout="LISTEN 0 1024 0.0.0.0:8443 0.0.0.0:*\n")
        raise AssertionError(command)

    connection.run.side_effect = run
    driver = NginxArtifactDriver(
        ServerDriverContext(
            plan=plan,
            cluster=ClusterConfig(),
            panel=MagicMock(),
            connection_for=lambda _ref: connection,
            server_addresses={"srv-exit": "198.51.100.10"},
        )
    )
    return driver, action, connection


def test_nginx_missing_artifact_is_confirmed_absent() -> None:
    driver, action, connection = _nginx_observation_case()
    connection.get_text.return_value = _result(returncode=1, stderr="cat: artifact: No such file or directory")

    observation = driver.observe(action, None)

    assert observation.exists is False


def test_nginx_unreadable_artifact_is_unavailable_evidence() -> None:
    driver, action, connection = _nginx_observation_case()
    connection.get_text.return_value = _result(returncode=1, stderr="cat: artifact: Permission denied")

    with pytest.raises(ResourceReconcileError, match="nginx artifact evidence is unavailable"):
        driver.observe(action, None)


def test_nginx_inactive_service_is_drift() -> None:
    driver, action, connection = _nginx_observation_case()
    connection.run.side_effect = [
        _result(returncode=3, stdout="inactive\n"),
        _result(stdout="LISTEN 0 1024 0.0.0.0:8443 0.0.0.0:*\n"),
    ]

    assert not observation_converges(action, driver.observe(action, None))


def test_nginx_rejects_systemd_daemon_failure() -> None:
    driver, action, connection = _nginx_observation_case()
    connection.run.side_effect = [
        _result(returncode=1, stderr="Failed to connect to bus"),
        _result(stdout="LISTEN 0 1024 0.0.0.0:8443 0.0.0.0:*\n"),
    ]

    with pytest.raises(ResourceReconcileError, match="nginx service evidence is unavailable"):
        driver.observe(action, None)


def test_nginx_rejects_failed_listener_inspection() -> None:
    driver, action, connection = _nginx_observation_case()
    connection.run.side_effect = [
        _result(stdout="active\n"),
        _result(returncode=127, stderr="ss: command not found"),
    ]

    with pytest.raises(ResourceReconcileError, match="TCP listener inspection evidence is unavailable"):
        driver.observe(action, None)


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
    intent_hash = "1" * 64
    plan = ResourcePlan(
        intent_hash=intent_hash,
        plan_hash=compute_plan_hash(
            compiler_version=COMPILER_VERSION,
            intent_hash=intent_hash,
            resources=[binding, runtime],
        ),
        resources=[binding, runtime],
    )
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
    driver = NodeRuntimeDriver(
        ServerDriverContext(
            plan=plan,
            cluster=cluster,
            panel=panel,
            connection_for=lambda _ref: MagicMock(spec=ServerConnection),
            server_addresses={"srv-exit": "198.51.100.10"},
            node_secrets={"exit-a": "node-secret"},
        )
    )
    return driver, action, panel


def test_node_runtime_missing_container_is_confirmed_drift() -> None:
    driver, action, panel = _node_runtime_case()
    connection = MagicMock(spec=ServerConnection)
    connection.get_text.return_value = _result(stdout="compose\n")
    connection.run.return_value = _result(returncode=1, stdout="Error: No such object: remnawave-node\n")
    driver.context.connection_for = lambda _ref: connection
    panel.get_node.return_value = SimpleNamespace(is_connected=True)

    observation = driver.observe(action, None)

    assert not observation_converges(action, observation)


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
    driver, action, panel = _node_runtime_case()
    connection = MagicMock(spec=ServerConnection)
    connection.get_text.return_value = _result(stdout="compose\n")
    connection.run.return_value = _result(returncode=returncode, stderr=output)
    driver.context.connection_for = lambda _ref: connection
    panel.get_node.return_value = SimpleNamespace(is_connected=True)

    with pytest.raises(ResourceReconcileError, match="Docker container remnawave-node evidence is unavailable"):
        driver.observe(action, None)


def test_node_runtime_missing_managed_warp_binary_is_drift() -> None:
    driver, action, panel = _node_runtime_case(warp=True)
    connection = MagicMock(spec=ServerConnection)
    connection.get_text.return_value = _result(stdout="compose\n")
    connection.run.side_effect = [
        _result(stdout="true\n"),
        _result(returncode=127, stderr="warp-cli: command not found"),
    ]
    driver.context.connection_for = lambda _ref: connection
    panel.get_node.return_value = SimpleNamespace(is_connected=True)

    observation = driver.observe(action, None)

    assert not observation_converges(action, observation)


def test_node_runtime_rejects_warp_daemon_failure() -> None:
    driver, action, panel = _node_runtime_case(warp=True)
    connection = MagicMock(spec=ServerConnection)
    connection.get_text.return_value = _result(stdout="compose\n")
    connection.run.side_effect = [
        _result(stdout="true\n"),
        _result(returncode=1, stderr="Cannot connect to daemon"),
    ]
    driver.context.connection_for = lambda _ref: connection
    panel.get_node.return_value = SimpleNamespace(is_connected=True)

    with pytest.raises(ResourceReconcileError, match="WARP status evidence is unavailable"):
        driver.observe(action, None)


def test_node_runtime_waits_for_bound_node_to_connect() -> None:
    driver, action, panel = _node_runtime_case()

    with (
        patch("meridian.reconciler.server_drivers.deploy_node_container", return_value=True),
        patch("meridian.reconciler.server_drivers.wait_for_node_connected", return_value=True) as wait,
    ):
        driver.apply(action, None)

    wait.assert_called_once_with(panel, "node-uuid")


def test_node_runtime_fails_when_bound_node_does_not_connect() -> None:
    driver, action, _panel = _node_runtime_case()

    with (
        patch("meridian.reconciler.server_drivers.deploy_node_container", return_value=True),
        patch("meridian.reconciler.server_drivers.wait_for_node_connected", return_value=False),
        pytest.raises(ResourceReconcileError, match="did not connect to the panel"),
    ):
        driver.apply(action, None)


def test_port_443_stream_apply_retires_the_legacy_artifact(
    monkeypatch,
) -> None:
    resource = make_resource(
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
    intent_hash = "1" * 64
    plan = ResourcePlan(
        intent_hash=intent_hash,
        plan_hash=compute_plan_hash(
            compiler_version=COMPILER_VERSION,
            intent_hash=intent_hash,
            resources=[resource],
        ),
        resources=[resource],
    )
    action = build_resource_actions(plan, 1)[0]
    connection = MagicMock(spec=ServerConnection)
    artifact_path = nginx_artifact_path(resource.logical_id, "stream")

    def get_text(path: str, *, timeout: int):
        if path == "/etc/nginx/nginx.conf":
            return SimpleNamespace(
                returncode=0,
                stdout="stream { include /etc/nginx/stream.d/*.conf; }\n",
            )
        if path == artifact_path:
            return SimpleNamespace(returncode=1, stdout="")
        if path == "/etc/nginx/stream.d/meridian.conf":
            return SimpleNamespace(returncode=0, stdout="legacy\n")
        raise AssertionError(path)

    connection.get_text.side_effect = get_text
    connection.run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(
        "meridian.reconciler.server_drivers.InstallNginx.run",
        lambda *_args, **_kwargs: StepResult(name="Install nginx", status="ok"),
    )
    monkeypatch.setattr(
        "meridian.reconciler.server_drivers.ensure_file_content",
        lambda *_args, **_kwargs: SimpleNamespace(ok=True, result=None),
    )
    driver = NginxArtifactDriver(
        ServerDriverContext(
            plan=plan,
            cluster=ClusterConfig(),
            panel=MagicMock(),
            connection_for=lambda _ref: connection,
            server_addresses={"srv-exit": "198.51.100.10"},
        )
    )

    driver.apply(action, None)

    assert any(call.args[0] == "rm -f /etc/nginx/stream.d/meridian.conf" for call in connection.run.call_args_list)
