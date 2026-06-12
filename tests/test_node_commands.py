"""Tests for node add/check/list/remove commands.

Nodes are proxy servers running Remnawave node + Xray.
Commands manage nodes via panel API and cluster.yml topology.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import (
    ClusterConfig,
    InboundRef,
    NodeEntry,
    PanelConfig,
    ProtocolKey,
    RelayEntry,
)
from meridian.commands.node import run_add, run_check, run_list, run_remove
from meridian.remnawave import Node, RemnawaveError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _configured_cluster() -> ClusterConfig:
    """Create a cluster with a panel node and an exit node."""
    return ClusterConfig(
        panel=PanelConfig(
            url="https://198.51.100.1/panel",
            api_token="tok",
            server_ip="198.51.100.1",
            secret_path="/secret",
        ),
        nodes=[
            NodeEntry(
                ip="198.51.100.1",
                uuid="550e8400-e29b-41d4-a716-446655440001",
                is_panel_host=True,
                name="panel-node",
            ),
            NodeEntry(
                ip="198.51.100.2",
                uuid="550e8400-e29b-41d4-a716-446655440002",
                is_panel_host=False,
                name="exit-node",
            ),
        ],
        inbounds={
            ProtocolKey.REALITY: InboundRef(
                uuid="550e8400-e29b-41d4-a716-446655440010",
                tag="vless-reality",
            ),
        },
    )


def _cluster_with_relay() -> ClusterConfig:
    """Cluster with a relay that depends on the exit node."""
    c = _configured_cluster()
    c.relays = [
        RelayEntry(
            ip="198.51.100.3",
            exit_node_ip="198.51.100.2",
            name="relay",
        ),
    ]
    return c


def _make_api_node(
    uuid: str = "550e8400-e29b-41d4-a716-446655440002",
    connected: bool = True,
    disabled: bool = False,
) -> Node:
    """Create a mock API node response."""
    return Node(
        uuid=uuid,
        name="exit-node",
        address="198.51.100.2",
        port=443,
        is_connected=connected,
        is_disabled=disabled,
        xray_version="1.8.4",
        traffic_used=1024 * 1024 * 500,
    )


def _make_panel_mock() -> MagicMock:
    """Create a MeridianPanel mock with sensible defaults."""
    panel = MagicMock()
    panel.list_nodes.return_value = [
        _make_api_node(uuid="550e8400-e29b-41d4-a716-446655440001"),
        _make_api_node(uuid="550e8400-e29b-41d4-a716-446655440002"),
    ]
    panel.get_node.return_value = _make_api_node()
    panel.disable_node.return_value = None
    panel.delete_node.return_value = None
    panel.ping.return_value = True
    return panel


def _ssh_result(stdout: str = "", rc: int = 0) -> subprocess.CompletedProcess[str]:
    """Build a fake SSH CompletedProcess."""
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr="")


@pytest.fixture
def _patch_cluster_config(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Ensure CLUSTER_CONFIG points into the tmp home directory."""
    import meridian.config as cfg

    cluster_path = tmp_home / "cluster.yml"
    monkeypatch.setattr(cfg, "CLUSTER_CONFIG", cluster_path)
    monkeypatch.setattr(cfg, "CLUSTER_BACKUP", tmp_home / "cluster.yml.bak")
    return cluster_path


# ---------------------------------------------------------------------------
# TestNodeAdd
# ---------------------------------------------------------------------------


class TestNodeAdd:
    def test_add_duplicate_ip_fails(self, tmp_home: Path) -> None:
        """Adding a node whose IP already exists in the cluster should fail."""
        cluster = _configured_cluster()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = cluster
            # 198.51.100.2 is already the exit-node
            run_add(ip="198.51.100.2", yes=True)

    def test_add_calls_provisioner(self, tmp_home: Path, _patch_cluster_config: Path) -> None:
        """Adding a new node should call _run_provisioner with the resolved server."""
        cluster = _configured_cluster()
        resolved = MagicMock()
        resolved.ip = "198.51.100.5"

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands.resolve.resolve_server", return_value=resolved),
            patch("meridian.commands.resolve._ensure_server_connection", return_value=resolved),
            patch("meridian.panel_bootstrap.run_provisioner") as mock_prov,
            patch("meridian.panel_bootstrap.setup_new_node"),
        ):
            mock_load.return_value = cluster
            run_add(ip="198.51.100.5", yes=True)

        mock_prov.assert_called_once()
        call_kwargs = mock_prov.call_args
        assert call_kwargs.kwargs["resolved"] is resolved
        assert call_kwargs.kwargs["is_panel_host"] is False

    def test_add_calls_setup_new_node(self, tmp_home: Path, _patch_cluster_config: Path) -> None:
        """Adding a new node should call _setup_new_node after provisioning."""
        cluster = _configured_cluster()
        resolved = MagicMock()
        resolved.ip = "198.51.100.5"

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands.resolve.resolve_server", return_value=resolved),
            patch("meridian.commands.resolve._ensure_server_connection", return_value=resolved),
            patch("meridian.panel_bootstrap.run_provisioner"),
            patch("meridian.panel_bootstrap.setup_new_node") as mock_setup,
        ):
            mock_load.return_value = cluster
            run_add(ip="198.51.100.5", name="new-node", yes=True)

        mock_setup.assert_called_once()
        call_kwargs = mock_setup.call_args
        assert call_kwargs.kwargs["resolved"] is resolved


# ---------------------------------------------------------------------------
# TestNodeCheck
# ---------------------------------------------------------------------------


class TestNodeCheck:
    def test_check_connected_node(self, tmp_home: Path) -> None:
        """Checking a connected node should report panel connected status."""
        cluster = _configured_cluster()
        panel = _make_panel_mock()
        panel.get_node.return_value = _make_api_node(connected=True)

        mock_conn = MagicMock()
        mock_conn.check_ssh.return_value = None
        # Docker containers
        mock_conn.run.side_effect = [
            _ssh_result("remnawave-node\nnginx\n"),  # docker ps
            _ssh_result("1\n"),  # port 443
            _ssh_result("notAfter=Dec 31 23:59:59 2026 GMT\n"),  # TLS
            _ssh_result("42\n"),  # disk
        ]

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.ssh.ServerConnection", return_value=mock_conn),
        ):
            mock_load.return_value = cluster
            run_check(ip_or_name="198.51.100.2")

        panel.get_node.assert_called_once_with("550e8400-e29b-41d4-a716-446655440002")

    def test_check_disconnected_node(self, tmp_home: Path) -> None:
        """Checking a disconnected node should report disconnected status."""
        cluster = _configured_cluster()
        panel = _make_panel_mock()
        panel.get_node.return_value = _make_api_node(connected=False)

        mock_conn = MagicMock()
        mock_conn.check_ssh.return_value = None
        mock_conn.run.side_effect = [
            _ssh_result("remnawave-node\n"),  # docker ps
            _ssh_result("1\n"),  # port 443
            _ssh_result("notAfter=Dec 31 23:59:59 2026 GMT\n"),  # TLS
            _ssh_result("42\n"),  # disk
        ]

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.ssh.ServerConnection", return_value=mock_conn),
        ):
            mock_load.return_value = cluster
            # Should not raise — disconnected is reported, not fatal
            run_check(ip_or_name="exit-node")

    def test_check_node_not_found_fails(self, tmp_home: Path) -> None:
        """Checking a node that doesn't exist should fail."""
        cluster = _configured_cluster()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = cluster
            run_check(ip_or_name="198.51.100.99")

    def test_check_panel_unreachable(self, tmp_home: Path) -> None:
        """If the panel API is unreachable, check should report it and continue SSH checks."""
        cluster = _configured_cluster()
        panel = _make_panel_mock()
        panel.__enter__ = MagicMock(return_value=panel)
        panel.__exit__ = MagicMock(return_value=False)
        panel.get_node.side_effect = RemnawaveError("Connection refused")

        mock_conn = MagicMock()
        mock_conn.check_ssh.return_value = None
        mock_conn.run.side_effect = [
            _ssh_result("remnawave-node\n"),
            _ssh_result("1\n"),
            _ssh_result("notAfter=Dec 31 23:59:59 2026 GMT\n"),
            _ssh_result("42\n"),
        ]

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.ssh.ServerConnection", return_value=mock_conn),
        ):
            mock_load.return_value = cluster
            # Should not raise — panel unreachable is reported, SSH checks continue
            run_check(ip_or_name="198.51.100.2")

        # SSH checks still ran
        mock_conn.check_ssh.assert_called_once()


# ---------------------------------------------------------------------------
# TestNodeRemove
# ---------------------------------------------------------------------------


class TestNodeRemove:
    def test_remove_existing_node(self, tmp_home: Path, _patch_cluster_config: Path) -> None:
        """Removing a valid exit node should disable + delete from panel and save cluster."""
        cluster = _configured_cluster()
        panel = _make_panel_mock()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.commands.node.confirm", return_value=True),
        ):
            mock_load.return_value = cluster
            run_remove(ip_or_name="198.51.100.2")

        panel.disable_node.assert_called_once_with("550e8400-e29b-41d4-a716-446655440002")
        panel.delete_node.assert_called_once_with("550e8400-e29b-41d4-a716-446655440002")

    def test_remove_node_not_found_fails(self, tmp_home: Path) -> None:
        """Removing a node that doesn't exist should fail."""
        cluster = _configured_cluster()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = cluster
            run_remove(ip_or_name="198.51.100.99")

    def test_remove_panel_node_blocked(self, tmp_home: Path) -> None:
        """Cannot remove the panel host node — should fail."""
        cluster = _configured_cluster()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = cluster
            run_remove(ip_or_name="198.51.100.1")

    def test_remove_with_dependent_relays_blocked(self, tmp_home: Path) -> None:
        """Node with dependent relays cannot be removed without --force."""
        cluster = _cluster_with_relay()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = cluster
            run_remove(ip_or_name="198.51.100.2")

    def test_remove_with_dependent_relays_force_warns(self, tmp_home: Path, _patch_cluster_config: Path) -> None:
        """Node with dependent relays can be removed with --force (warns)."""
        cluster = _cluster_with_relay()
        panel = _make_panel_mock()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.commands.node.confirm", return_value=True),
            patch("meridian.commands.node.warn") as mock_warn,
            patch.object(ClusterConfig, "save"),
        ):
            mock_load.return_value = cluster
            run_remove(ip_or_name="198.51.100.2", force=True)

        # Should have warned about dependent relays
        mock_warn.assert_any_call("Force-removing node with 1 dependent relay(s): relay")
        # Should still proceed with panel deletion
        panel.delete_node.assert_called_once()

    def test_remove_saves_cluster_without_node(self, tmp_home: Path, _patch_cluster_config: Path) -> None:
        """After removal, the cluster should be saved without the removed node."""
        cluster = _configured_cluster()
        panel = _make_panel_mock()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.commands.node.confirm", return_value=True),
        ):
            mock_load.return_value = cluster
            run_remove(ip_or_name="198.51.100.2")

        # Only the panel node should remain
        assert len(cluster.nodes) == 1
        assert cluster.nodes[0].ip == "198.51.100.1"

    def test_remove_panel_api_error_warns(self, tmp_home: Path, _patch_cluster_config: Path) -> None:
        """Panel API errors during disable/delete should warn but still remove from cluster."""
        cluster = _configured_cluster()
        panel = _make_panel_mock()
        panel.disable_node.side_effect = RemnawaveError("Panel down")
        panel.delete_node.side_effect = RemnawaveError("Panel down")

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.commands.node.confirm", return_value=True),
        ):
            mock_load.return_value = cluster
            # Should not raise — API errors are caught and warned
            run_remove(ip_or_name="198.51.100.2")

        # Node should still be removed from cluster despite API errors
        assert len(cluster.nodes) == 1
        assert cluster.nodes[0].ip == "198.51.100.1"


# ---------------------------------------------------------------------------
# TestNodeList
# ---------------------------------------------------------------------------


class TestNodeList:
    def test_list_shows_nodes(self, tmp_home: Path) -> None:
        """Listing nodes should query the panel API and not fail."""
        cluster = _configured_cluster()
        panel = _make_panel_mock()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
        ):
            mock_load.return_value = cluster
            run_list()

        panel.list_nodes.assert_called_once()

    def test_list_api_error_fails(self, tmp_home: Path) -> None:
        """If the panel API fails, list should exit with an error."""
        cluster = _configured_cluster()
        panel = _make_panel_mock()
        panel.list_nodes.side_effect = RemnawaveError("Panel unreachable")

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = cluster
            run_list()
