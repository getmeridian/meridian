"""Focused server-side resource driver contracts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from meridian.cluster import ClusterConfig
from meridian.compiler.models import (
    COMPILER_VERSION,
    NginxArtifactPayload,
    NginxRouteSpec,
    ResourcePlan,
    ServerBaselinePayload,
    canonical_hash,
    compute_plan_hash,
    make_resource,
)
from meridian.provision.steps import StepResult
from meridian.reconciler.resources import UnknownResourceOutcome, build_resource_actions, observation_converges
from meridian.reconciler.server_drivers import (
    NginxArtifactDriver,
    ServerBaselineDriver,
    ServerDriverContext,
)
from meridian.reconciler.server_render import nginx_artifact_path
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
