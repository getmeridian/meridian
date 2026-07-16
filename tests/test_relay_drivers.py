"""V4 relay artifacts are executable, isolated, and rollback-safe."""

from __future__ import annotations

import subprocess
from typing import cast

import pytest

from meridian.cluster import ClusterConfig
from meridian.compiler.models import (
    COMPILER_VERSION,
    NginxArtifactPayload,
    NginxRouteSpec,
    RealmHopPayload,
    ResourcePlan,
    ResourcePostcondition,
    compute_plan_hash,
    make_resource,
)
from meridian.provision.steps import StepResult
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceReconcileError,
    UnknownResourceOutcome,
    build_resource_actions,
)
from meridian.reconciler.server_drivers import RealmHopDriver, ServerDriverContext
from meridian.reconciler.server_render import (
    realm_config_path,
    realm_service_name,
    render_nginx_artifact,
    render_realm_config,
)
from meridian.remnawave import MeridianPanel
from meridian.ssh import ServerConnection

ADDRESSES = {
    "srv-exit": "198.51.100.10",
    "srv-relay-core": "198.51.100.20",
    "srv-relay-edge": "198.51.100.30",
}


class StatefulConnection:
    def __init__(
        self,
        *,
        files: dict[str, str] | None = None,
        fail_first_restart: bool = False,
        restart_returncode: int = 1,
    ) -> None:
        self.files = dict(files or {})
        self.calls: list[str] = []
        self.fail_first_restart = fail_first_restart
        self.restart_returncode = restart_returncode
        self.restart_count = 0
        self.ip = "198.51.100.30"
        self.user = "root"
        self.local_mode = False
        self.needs_sudo = False

    def run(
        self,
        command: str,
        timeout: int = 30,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        if command.startswith("systemctl restart "):
            self.restart_count += 1
            if self.fail_first_restart and self.restart_count == 1:
                return _result(returncode=self.restart_returncode, stderr="restart failed")
        return _result()

    def get_text(
        self,
        path: str,
        timeout: int = 30,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if path not in self.files:
            return _result(returncode=1)
        return _result(stdout=self.files[path])

    def put_text(
        self,
        path: str,
        text: str,
        timeout: int = 30,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        self.files[path] = text
        return _result()


def test_realm_chain_configs_target_the_next_hop_downstream() -> None:
    core = RealmHopPayload(
        chain_ref="relay-a",
        hop_index=0,
        server_ref="srv-relay-core",
        listen_port=443,
        target_server_ref="srv-exit",
        target_port=443,
        advertised=False,
    )
    edge = RealmHopPayload(
        chain_ref="relay-a",
        hop_index=1,
        server_ref="srv-relay-edge",
        listen_port=443,
        target_server_ref="srv-relay-core",
        target_port=443,
        advertised=True,
    )

    core_config = render_realm_config(core, ADDRESSES)
    edge_config = render_realm_config(edge, ADDRESSES)

    assert 'remote = "198.51.100.10:443"' in core_config
    assert 'remote = "198.51.100.20:443"' in edge_config
    assert "198.51.100.30" not in edge_config


def test_stream_artifact_accepts_custom_sni_and_preserves_camouflage_fallback() -> None:
    payload = NginxArtifactPayload(
        server_ref="srv-exit",
        listener_port=443,
        layer="stream",
        fallback_server_name="www.microsoft.com",
        routes=[
            NginxRouteSpec(
                match="sni",
                server_names=["relay.example.com"],
                backend_server_ref="srv-exit",
                backend_port=10443,
                protocol="reality",
            ),
            NginxRouteSpec(
                match="sni",
                server_names=["www.microsoft.com"],
                backend_server_ref="srv-exit",
                backend_port=10443,
                protocol="reality",
            ),
        ],
    )

    rendered = render_nginx_artifact("nginx:srv-exit:stream:443", payload, ADDRESSES)

    assert "relay.example.com meridian_upstream_" in rendered
    assert "www.microsoft.com meridian_upstream_" in rendered
    assert "server www.microsoft.com:443;" in rendered
    assert "default meridian_fallback_" in rendered
    assert "server 127.0.0.1:10443;" in rendered


def test_stream_artifact_routes_no_sni_to_local_control_https() -> None:
    payload = NginxArtifactPayload(
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
    )

    rendered = render_nginx_artifact("nginx:srv-exit:stream:443", payload, ADDRESSES)

    assert '    "" meridian_upstream_' in rendered
    assert "server 127.0.0.1:8443;" in rendered


def test_realm_restart_failure_restores_previous_files(monkeypatch: pytest.MonkeyPatch) -> None:
    plan, action = _realm_plan_and_action()
    config_path = realm_config_path(action.resource.logical_id)
    unit_path = f"/etc/systemd/system/{realm_service_name(action.resource.logical_id)}.service"
    conn = StatefulConnection(
        files={
            config_path: "previous config\n",
            unit_path: "previous unit\n",
        },
        fail_first_restart=True,
    )
    context = ServerDriverContext(
        plan=plan,
        cluster=ClusterConfig(),
        panel=cast(MeridianPanel, object()),
        connection_for=lambda _server_ref: cast(ServerConnection, conn),
        server_addresses=ADDRESSES,
    )
    monkeypatch.setattr(
        "meridian.reconciler.server_drivers.InstallRealm.run",
        lambda *_args, **_kwargs: StepResult(name="Install Realm", status="unchanged"),
    )

    with pytest.raises(ResourceReconcileError, match="previous configuration restored"):
        RealmHopDriver(context).apply(action, None)

    assert conn.files[config_path] == "previous config\n"
    assert conn.files[unit_path] == "previous unit\n"
    assert conn.restart_count == 2


def test_realm_services_are_isolated_by_logical_identity() -> None:
    first = "realm:relay-a:0"
    second = "realm:relay-b:0"

    assert realm_service_name(first) != realm_service_name(second)
    assert realm_config_path(first) != realm_config_path(second)


def test_realm_restart_timeout_is_reobserved_without_guessing_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, action = _realm_plan_and_action()
    config_path = realm_config_path(action.resource.logical_id)
    unit_path = f"/etc/systemd/system/{realm_service_name(action.resource.logical_id)}.service"
    conn = StatefulConnection(
        files={
            config_path: "previous config\n",
            unit_path: "previous unit\n",
        },
        fail_first_restart=True,
        restart_returncode=124,
    )
    context = ServerDriverContext(
        plan=plan,
        cluster=ClusterConfig(),
        panel=cast(MeridianPanel, object()),
        connection_for=lambda _server_ref: cast(ServerConnection, conn),
        server_addresses=ADDRESSES,
    )
    monkeypatch.setattr(
        "meridian.reconciler.server_drivers.InstallRealm.run",
        lambda *_args, **_kwargs: StepResult(name="Install Realm", status="unchanged"),
    )

    with pytest.raises(UnknownResourceOutcome, match="observation is required"):
        RealmHopDriver(context).apply(action, None)

    assert conn.files[config_path] != "previous config\n"
    assert conn.files[unit_path] != "previous unit\n"
    assert conn.restart_count == 1


def _realm_plan_and_action() -> tuple[ResourcePlan, ResourceAction]:
    resource = make_resource(
        "realm:relay-a:1",
        RealmHopPayload(
            chain_ref="relay-a",
            hop_index=1,
            server_ref="srv-relay-edge",
            listen_port=443,
            target_server_ref="srv-relay-core",
            target_port=443,
            advertised=True,
        ),
        postconditions=[
            ResourcePostcondition(
                kind="listening",
                target_ref="srv-relay-edge",
                detail="tcp:443",
            )
        ],
    )
    intent_hash = "a" * 64
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


def _result(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )
