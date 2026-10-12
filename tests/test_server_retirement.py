"""Server-side retirement removes only Meridian-tagged artifacts on the recorded server."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from meridian.cluster import ManagedResourceBinding
from meridian.reconciler.resources import ResourceReconcileError
from meridian.reconciler.server_retirement import FirewallRetirement, NginxArtifactRetirement
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
