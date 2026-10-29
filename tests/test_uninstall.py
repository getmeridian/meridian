"""Failure-safety tests for ``meridian teardown``."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.uninstall import _remove_saved_server_or_fail, _topology_references, run
from meridian.servers import ServerEntry
from meridian.setup.single_server import single_server_intent
from tests.support.topology import single_exit_intent


def test_teardown_bad_server_selector_fails_without_prompt_fallback() -> None:
    registry = MagicMock()

    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", side_effect=typer.Exit(2)) as resolve,
        pytest.raises(typer.Exit) as exc_info,
    ):
        run("typo", yes=True)

    assert exc_info.value.exit_code == 2
    resolve.assert_called_once_with(registry, explicit_ip="typo", user="", port=0)


def test_teardown_declined_confirmation_exits_one() -> None:
    cluster = ClusterConfig()
    resolved = SimpleNamespace(ip="198.51.100.10", user="root", conn=MagicMock())

    with (
        patch("meridian.commands.uninstall.ServerRegistry"),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.uninstall.confirm", return_value=False),
        patch("meridian.commands.uninstall.ensure_server_connection") as ensure_connection,
        pytest.raises(typer.Exit) as exc_info,
    ):
        run(ip=resolved.ip)

    assert exc_info.value.exit_code == 1
    ensure_connection.assert_not_called()


def test_teardown_reports_saved_profile_failure_after_remote_completion() -> None:
    registry = MagicMock()
    registry.remove.side_effect = OSError("disk full")

    with pytest.raises(typer.Exit) as exc_info:
        _remove_saved_server_or_fail(registry, "198.51.100.10")

    assert exc_info.value.exit_code == 3


@pytest.mark.parametrize("failure_point", ["backup", "unlink"])
def test_panel_teardown_local_state_failure_reports_remote_completion(failure_point: str) -> None:
    cluster = ClusterConfig(
        panel=PanelConfig(
            url="https://198.51.100.1/panel",
            api_token="token",
            server_ip="198.51.100.1",
        ),
        topology_intent=single_server_intent(None, server_ref="srv-only"),
    )
    registry = MagicMock()
    registry.find.return_value = ServerEntry(id="srv-only", host="198.51.100.1")
    resolved = SimpleNamespace(ip="198.51.100.1", user="root", conn=MagicMock())
    cluster_path = MagicMock()
    if failure_point == "unlink":
        cluster_path.unlink.side_effect = OSError("permission denied")
    backup_error = OSError("disk full") if failure_point == "backup" else None

    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.commands.uninstall.ensure_server_connection", return_value=resolved),
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch("meridian.config.CLUSTER_CONFIG", cluster_path),
        patch.object(cluster, "backup", side_effect=backup_error),
        patch("meridian.provision.steps.Provisioner") as provisioner,
        pytest.raises(typer.Exit) as exc_info,
    ):
        provisioner.return_value.run.return_value = [SimpleNamespace(status="changed", detail="")]
        run(ip=resolved.ip, yes=True)

    assert exc_info.value.exit_code == 3
    provisioner.return_value.run.assert_called_once()
    registry.remove.assert_not_called()


def test_v4_teardown_fails_before_ssh_or_remote_mutation() -> None:
    cluster = ClusterConfig(topology_intent=single_exit_intent(users=["alice"]))
    registry = MagicMock()
    registry.find.return_value = ServerEntry(
        id="srv-exit",
        host="198.51.100.2",
        name="exit-server",
    )
    resolved = SimpleNamespace(ip="198.51.100.2", user="root", conn=MagicMock())

    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.commands.uninstall.ensure_server_connection") as ensure_connection,
        patch("meridian.commands.uninstall.confirm") as confirmation,
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch("meridian.provision.steps.Provisioner") as provisioner,
        pytest.raises(typer.Exit),
    ):
        run(ip=resolved.ip, yes=False)

    ensure_connection.assert_not_called()
    confirmation.assert_not_called()
    provisioner.assert_not_called()
    registry.remove.assert_not_called()


def test_v4_teardown_of_a_single_server_fleet_removes_everything(tmp_home) -> None:
    from meridian.setup.single_server import single_server_intent

    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.2/panel", api_token="token", server_ip="198.51.100.2"),
        topology_intent=single_server_intent(None, server_ref="srv-only"),
    )
    (tmp_home / "cluster.yml").write_text("version: 3\n")
    (tmp_home / "setup.json").write_text("{}")
    registry = MagicMock()
    registry.find.return_value = ServerEntry(id="srv-only", host="198.51.100.2")
    resolved = SimpleNamespace(ip="198.51.100.2", user="root", conn=MagicMock())
    provisioner = MagicMock()
    provisioner.return_value.run.return_value = [SimpleNamespace(status="changed", detail="")]

    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.commands.uninstall.ensure_server_connection", return_value=resolved),
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch.object(cluster, "backup"),
        patch("meridian.provision.steps.Provisioner", provisioner),
    ):
        run(ip=resolved.ip, yes=True)

    provisioner.return_value.run.assert_called_once()
    assert not (tmp_home / "cluster.yml").exists()
    assert not (tmp_home / "setup.json").exists()
    registry.remove.assert_called_once_with(resolved.ip)


def test_v4_teardown_allows_server_after_role_and_workload_are_retired() -> None:
    cluster = ClusterConfig(topology_intent=single_exit_intent(exit_ref="srv-current-exit", users=["alice"]))
    retired = ServerEntry(id="srv-retired", host="198.51.100.9", name="retired")

    assert _topology_references(cluster, retired) == []


def _roleless_server_cluster() -> ClusterConfig:
    return ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/panel", api_token="token", server_ip="198.51.100.1"),
        topology_intent=single_server_intent(None, server_ref="srv-control"),
    )


def test_roleless_server_teardown_removes_its_saved_profile() -> None:
    cluster = _roleless_server_cluster()
    registry = MagicMock()
    registry.find.return_value = ServerEntry(id="srv-spare", host="198.51.100.2")
    resolved = SimpleNamespace(ip="198.51.100.2", user="root", conn=MagicMock())
    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.commands.uninstall.ensure_server_connection", return_value=resolved),
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch("meridian.provision.steps.Provisioner") as provisioner,
    ):
        provisioner.return_value.run.return_value = [SimpleNamespace(status="changed", detail="")]
        run(ip=resolved.ip, yes=True)

    registry.remove.assert_called_once_with("198.51.100.2")


def test_cluster_without_topology_is_refused_before_ssh() -> None:
    cluster = ClusterConfig(panel=PanelConfig(url="https://198.51.100.1/panel", api_token="token"))
    registry = MagicMock()
    resolved = SimpleNamespace(ip="198.51.100.1", user="root", conn=MagicMock())

    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.commands.uninstall.ensure_server_connection") as ensure_connection,
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        pytest.raises(typer.Exit) as exc_info,
    ):
        run(ip=resolved.ip, yes=True)

    assert exc_info.value.exit_code == 2
    ensure_connection.assert_not_called()
    registry.remove.assert_not_called()
