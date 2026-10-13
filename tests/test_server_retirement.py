"""Server-side retirement removes only Meridian-tagged artifacts on the recorded server."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from meridian.cluster import ManagedResourceBinding
from meridian.compiler.models import NodeRuntimePayload
from meridian.config import REMNAWAVE_NODE_DIR
from meridian.reconciler.resources import ResourceReconcileError
from meridian.reconciler.server_render import realm_config_path, realm_service_name
from meridian.reconciler.server_retirement import (
    FirewallRetirement,
    NginxArtifactRetirement,
    NodeRuntimeRetirement,
    RealmHopRetirement,
)
from tests.support.mock_connection import MockConnection

_MARKER = "meridian-v4-0123456789abcdef"
_STREAM = "/etc/nginx/stream.d/meridian-v4-0123456789abcdef.conf"
_HTTP = "/etc/nginx/sites-available/meridian-v4-fedcba9876543210.conf"


def _driver(cls, conn: MockConnection):
    driver = cls()
    driver.context = SimpleNamespace(connection=lambda server_ref: conn)
    return driver


def _binding(remote_id: str, server_ref: str = "srv-a") -> ManagedResourceBinding:
    return ManagedResourceBinding(logical_id="x", resource_kind="k", remote_id=remote_id, server_ref=server_ref)


def test_firewall_rules_are_deleted_by_marker_highest_first() -> None:
    conn = MockConnection()

    _driver(FirewallRetirement, conn).retire(_binding(_MARKER))

    (command,) = conn.calls
    assert f"# {_MARKER}$" in command and "sort -rn" in command and "ufw --force delete" in command


def test_http_artifact_removes_file_and_enabled_link_then_reloads() -> None:
    conn = MockConnection()

    _driver(NginxArtifactRetirement, conn).retire(_binding(_HTTP))

    (command,) = conn.calls
    assert _HTTP in command and _HTTP.replace("sites-available", "sites-enabled") in command
    assert command.index("nginx -t") < command.index("reload-or-restart nginx")


def test_failed_nginx_reload_is_reported() -> None:
    conn = MockConnection()
    conn.when("rm -f", rc=1, stdout="nginx: [emerg] bad config")

    with pytest.raises(ResourceReconcileError, match="did not reload"):
        _driver(NginxArtifactRetirement, conn).retire(_binding(_STREAM))


@pytest.mark.parametrize(
    ("cls", "remote_id"),
    [(FirewallRetirement, "meridian-v4-x; rm -rf /"), (NginxArtifactRetirement, "/etc/passwd")],
)
def test_unexpected_ids_are_refused_without_touching_the_server(cls, remote_id: str) -> None:
    conn = MockConnection()

    with pytest.raises(ResourceReconcileError, match="Unexpected"):
        _driver(cls, conn).retire(_binding(remote_id))
    assert conn.calls == []


def test_bindings_without_a_recorded_server_are_refused() -> None:
    with pytest.raises(ResourceReconcileError, match="before Meridian tracked its server"):
        _driver(FirewallRetirement, MockConnection()).retire(_binding(_MARKER, server_ref=""))


def _plan_with_node(server_ref: str) -> SimpleNamespace:
    payload = NodeRuntimePayload(workload_ref="exit-c", server_ref=server_ref, binding_ref="binding:exit-c")
    return SimpleNamespace(resources=[SimpleNamespace(payload=payload)])


def _runtime_driver(conn: MockConnection, *, plan: SimpleNamespace | None = None) -> NodeRuntimeRetirement:
    driver = NodeRuntimeRetirement()
    driver.context = SimpleNamespace(connection=lambda server_ref: conn, plan=plan or SimpleNamespace(resources=[]))
    return driver


def test_realm_hop_unit_and_config_are_removed_and_verified_stopped() -> None:
    conn = MockConnection()
    service = realm_service_name("realm:relay-chain:0")

    _driver(RealmHopRetirement, conn).retire(
        ManagedResourceBinding(logical_id="realm:relay-chain:0", remote_id=service, server_ref="srv-relay")
    )

    (command,) = conn.calls
    assert command.index(f"systemctl disable --now {service}") < command.index("rm -f")
    assert f"/etc/systemd/system/{service}.service" in command
    assert realm_config_path("realm:relay-chain:0") in command
    assert "daemon-reload" in command and command.endswith(f"! systemctl is-active --quiet {service}")


def test_realm_hop_still_running_after_removal_is_a_failure() -> None:
    conn = MockConnection()
    conn.when("systemctl disable", rc=1)
    service = realm_service_name("realm:relay-chain:1")

    with pytest.raises(ResourceReconcileError, match="still running"):
        _driver(RealmHopRetirement, conn).retire(
            ManagedResourceBinding(logical_id="realm:relay-chain:1", remote_id=service, server_ref="srv-relay")
        )


def test_realm_hop_refuses_a_service_name_that_does_not_match_its_resource() -> None:
    conn = MockConnection()

    with pytest.raises(ResourceReconcileError, match="Unexpected Realm service"):
        _driver(RealmHopRetirement, conn).retire(
            ManagedResourceBinding(logical_id="realm:relay-chain:0", remote_id="sshd", server_ref="srv-relay")
        )
    assert conn.calls == []


def test_node_runtime_container_and_compose_dir_are_removed() -> None:
    conn = MockConnection()

    _runtime_driver(conn).retire(
        ManagedResourceBinding(logical_id="node:exit-b", remote_id=REMNAWAVE_NODE_DIR, server_ref="srv-exit-b")
    )

    (command,) = conn.calls
    assert f"docker compose -f {REMNAWAVE_NODE_DIR}/docker-compose.yml down" in command
    assert "docker rm -f remnawave-node" in command
    assert f"rm -rf {REMNAWAVE_NODE_DIR};" in command
    assert "/opt/remnawave " not in command and "/opt/remnawave;" not in command
    # The pulled node image goes too; Docker itself (baseline-installed, maybe operator-used) stays.
    assert "docker images -q remnawave/node" in command and "docker image rm $ids" in command
    assert "apt-get" not in command and "purge" not in command


def test_node_runtime_replaced_on_the_same_server_is_only_forgotten() -> None:
    conn = MockConnection()

    _runtime_driver(conn, plan=_plan_with_node("srv-exit-b")).retire(
        ManagedResourceBinding(logical_id="node:exit-b", remote_id=REMNAWAVE_NODE_DIR, server_ref="srv-exit-b")
    )

    assert conn.calls == []


def test_node_runtime_removal_failure_is_reported() -> None:
    conn = MockConnection()
    conn.when("docker compose", rc=1, stderr="Cannot connect to the Docker daemon")

    with pytest.raises(ResourceReconcileError, match="Cannot connect to the Docker daemon"):
        _runtime_driver(conn).retire(
            ManagedResourceBinding(logical_id="node:exit-b", remote_id=REMNAWAVE_NODE_DIR, server_ref="srv-exit-b")
        )


def test_node_runtime_refuses_an_unexpected_directory() -> None:
    conn = MockConnection()

    with pytest.raises(ResourceReconcileError, match="Unexpected node runtime"):
        _runtime_driver(conn).retire(
            ManagedResourceBinding(logical_id="node:exit-b", remote_id="/", server_ref="srv-exit-b")
        )
    assert conn.calls == []
