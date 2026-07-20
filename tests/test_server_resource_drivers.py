"""Focused server-side resource driver contracts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler.models import (
    COMPILER_VERSION,
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
    FirewallRuleDriver,
    NginxArtifactDriver,
    NodeRuntimeDriver,
    ServerBaselineDriver,
    ServerDriverContext,
)
from meridian.reconciler.server_render import artifact_token, nginx_artifact_path
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


def _node_runtime_case() -> tuple[NodeRuntimeDriver, ResourceAction, MagicMock]:
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
