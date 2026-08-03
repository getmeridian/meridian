"""Tests for node add/check/list/remove commands.

Nodes are proxy servers running Remnawave node + Xray.
Commands manage nodes via panel API and cluster.yml topology.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

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
from meridian.commands._helpers import ReviewedApplyPersistenceError, persist_reviewed_apply
from meridian.commands.node import _render_check, run_add, run_check, run_list, run_remove
from meridian.console import set_json_mode
from meridian.core.errors import LocalStateError
from meridian.core.fleet import FleetTopology, TopologyNode, TopologyPanel
from meridian.core.topology import AccessIntent, ControlPlaneIntent, ExitIntent, ProtocolPathIntent, SetupIntent
from meridian.diagnostics import CheckResult
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


def _minimal_v4_intent() -> SetupIntent:
    return SetupIntent(
        control=ControlPlaneIntent(server_ref="srv-control"),
        exits=[
            ExitIntent(
                id="exit-a",
                server_ref="srv-exit",
                paths=[
                    ProtocolPathIntent(
                        id="reality-a",
                        protocol="reality",
                        reality_sni="www.example.com",
                    )
                ],
            )
        ],
        default_egress_ref="exit-a",
        access=AccessIntent(users=["default"]),
    )


def _projected_topology(*nodes: TopologyNode) -> FleetTopology:
    return FleetTopology(
        panel=TopologyPanel(
            url="https://198.51.100.1/panel",
            display_url="https://198.51.100.1/panel",
            server_ip="198.51.100.1",
            ssh_user="root",
            ssh_port=22,
            deployed_with="v4",
        ),
        nodes=list(nodes),
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
    panel.__enter__.return_value = panel
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
    def test_v4_persist_wraps_local_write_errors(self) -> None:
        cluster = ClusterConfig()

        with (
            patch.object(cluster, "save", side_effect=OSError("disk full")),
            pytest.raises(ReviewedApplyPersistenceError, match="disk full") as exc_info,
        ):
            persist_reviewed_apply(cluster)

        assert "Remote state may have changed" in str(exc_info.value)

    def test_v4_apply_state_error_is_rendered_at_command_boundary(self) -> None:
        cluster = ClusterConfig(topology_intent=_minimal_v4_intent())
        registry = MagicMock()
        registry.find.return_value = SimpleNamespace(id="srv-new", host="198.51.100.5", name="new-exit")
        resolved = SimpleNamespace(ip="198.51.100.5", user="root")

        with (
            patch("meridian.commands.node.load_cluster", return_value=cluster),
            patch("meridian.servers.ServerRegistry", return_value=registry),
            patch("meridian.commands.resolve.resolve_server", return_value=resolved),
            patch("meridian.commands.resolve.ensure_server_connection", return_value=resolved),
            patch(
                "meridian.setup.runtime.SetupRuntime.apply_intent",
                side_effect=LocalStateError("review state changed"),
            ),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_add(ip="198.51.100.5", name="new-exit", yes=True)

        assert exc_info.value.exit_code == 2
        registry.add.assert_called_once()

    def test_v4_apply_persistence_error_warns_remote_state_may_have_changed(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        cluster = ClusterConfig(topology_intent=_minimal_v4_intent())
        registry = MagicMock()
        registry.find.return_value = SimpleNamespace(id="srv-new", host="198.51.100.5", name="new-exit")
        resolved = SimpleNamespace(ip="198.51.100.5", user="root")

        with (
            patch("meridian.commands.node.load_cluster", return_value=cluster),
            patch("meridian.servers.ServerRegistry", return_value=registry),
            patch("meridian.commands.resolve.resolve_server", return_value=resolved),
            patch("meridian.commands.resolve.ensure_server_connection", return_value=resolved),
            patch(
                "meridian.setup.runtime.SetupRuntime.apply_intent",
                side_effect=ReviewedApplyPersistenceError(
                    "disk full. Remote state may have changed. Repair local state, then rerun "
                    "`meridian plan` and `meridian apply` to reconcile."
                ),
            ),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_add(ip="198.51.100.5", name="new-exit", yes=True)

        assert exc_info.value.exit_code == 3
        output = capsys.readouterr().err
        assert "Remote state may have changed" in output
        assert "meridian plan" in output
        assert "meridian apply" in output

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

    def test_add_reports_remote_change_when_setup_state_cannot_be_saved(
        self,
        tmp_home: Path,
        _patch_cluster_config: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        cluster = _configured_cluster()
        resolved = MagicMock(ip="198.51.100.5", user="root")

        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands.resolve.resolve_server", return_value=resolved),
            patch("meridian.commands.resolve._ensure_server_connection", return_value=resolved),
            patch("meridian.panel_bootstrap.run_provisioner"),
            patch("meridian.panel_bootstrap.setup_new_node", side_effect=OSError("disk full")),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_add(ip="198.51.100.5", yes=True)

        assert exc_info.value.exit_code == 3
        output = capsys.readouterr().err
        assert "provisioned remotely" in output
        assert "Remote state changed" in output

    def test_add_reports_remote_change_when_desired_state_mirror_cannot_be_saved(
        self,
        tmp_home: Path,
        _patch_cluster_config: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        cluster = _configured_cluster()
        cluster.desired_nodes = []
        resolved = MagicMock(ip="198.51.100.5", user="root")

        def add_remote_node(**_kwargs: object) -> None:
            cluster.nodes.append(NodeEntry(ip="198.51.100.5", name="new-node"))

        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands.resolve.resolve_server", return_value=resolved),
            patch("meridian.commands.resolve._ensure_server_connection", return_value=resolved),
            patch("meridian.panel_bootstrap.run_provisioner"),
            patch("meridian.panel_bootstrap.setup_new_node", side_effect=add_remote_node),
            patch.object(cluster, "save", side_effect=OSError("read-only filesystem")),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_add(ip="198.51.100.5", name="new-node", yes=True)

        assert exc_info.value.exit_code == 3
        output = capsys.readouterr().err
        assert "provisioned remotely" in output
        assert "read-only filesystem" in output


# ---------------------------------------------------------------------------
# TestNodeCheck
# ---------------------------------------------------------------------------


class TestNodeCheck:
    def test_check_v4_routing_gateway_uses_node_runtime_checks(self) -> None:
        cluster = ClusterConfig(topology_intent=_minimal_v4_intent())
        topology = _projected_topology(
            TopologyNode(
                ip="198.51.100.30",
                name="gateway-a",
                uuid="gateway-a",
                is_panel_host=False,
                ssh_user="root",
                ssh_port=22,
                domain="",
                sni="www.example.com",
                xhttp_path="",
                ws_path="",
                role="routing_gateway",
            )
        )
        panel = _make_panel_mock()
        panel.get_node.return_value = _make_api_node(connected=True)
        connection = MagicMock()
        passed = CheckResult(name="check", status="passed", detail="healthy")
        deployment = SimpleNamespace(
            kind="v4",
            public_tcp_ports=(443,),
            public_udp_ports=(),
            internal_ports={},
            public_tls_ports=(443,),
            https_routes=(SimpleNamespace(port=443, tls_sni="www.example.com", host_header=""),),
        )

        with (
            patch("meridian.commands.node.load_cluster", return_value=cluster),
            patch("meridian.commands.node.topology_from_local_cluster", return_value=topology),
            patch("meridian.commands.node.make_panel", return_value=panel),
            patch("meridian.ssh.ServerConnection", return_value=connection),
            patch("meridian.diagnostics.check_container_running", return_value=passed) as container_check,
            patch("meridian.diagnostics.check_port_listening", return_value=passed) as port_check,
            patch("meridian.diagnostics.check_tls_certificate", return_value=passed) as tls_check,
            patch("meridian.diagnostics.check_disk_space", return_value=passed),
            patch("meridian.verification.deployment_verification_context", return_value=deployment),
        ):
            run_check(ip_or_name="gateway-a")

        container_check.assert_called_once_with(connection, "remnawave-node")
        port_check.assert_called_once_with(connection, 443, transport="tcp")
        tls_check.assert_called_once_with(connection, "www.example.com", port=443)

    def test_check_v4_uses_compiled_public_and_allocated_internal_ports(self) -> None:
        cluster = ClusterConfig(topology_intent=_minimal_v4_intent())
        topology = _projected_topology(
            TopologyNode(
                ip="198.51.100.30",
                name="gateway-a",
                uuid="gateway-a",
                is_panel_host=False,
                ssh_user="root",
                ssh_port=22,
                domain="edge.example.com",
                sni="www.example.com",
                xhttp_path="",
                ws_path="",
                role="routing_gateway",
            )
        )
        panel = _make_panel_mock()
        panel.get_node.return_value = _make_api_node(connected=True)
        connection = MagicMock()
        passed = CheckResult(name="check", status="passed", detail="healthy")
        deployment = SimpleNamespace(
            kind="v4",
            public_tcp_ports=(7443,),
            public_udp_ports=(9443,),
            internal_ports={"gateway-a/reality": 39001},
            public_tls_ports=(7443,),
            https_routes=(SimpleNamespace(port=7443, tls_sni="edge.example.com", host_header=""),),
        )

        with (
            patch("meridian.commands.node.load_cluster", return_value=cluster),
            patch("meridian.commands.node.topology_from_local_cluster", return_value=topology),
            patch("meridian.commands.node.make_panel", return_value=panel),
            patch("meridian.ssh.ServerConnection", return_value=connection),
            patch("meridian.diagnostics.check_container_running", return_value=passed),
            patch("meridian.diagnostics.check_port_listening", return_value=passed) as port_check,
            patch("meridian.diagnostics.check_tls_certificate", return_value=passed) as tls_check,
            patch("meridian.diagnostics.check_disk_space", return_value=passed),
            patch("meridian.verification.deployment_verification_context", return_value=deployment),
        ):
            run_check(ip_or_name="gateway-a")

        assert port_check.call_args_list == [
            call(connection, 7443, transport="tcp"),
            call(connection, 9443, transport="udp"),
            call(connection, 39001, transport="any"),
        ]
        tls_check.assert_called_once_with(connection, "edge.example.com", port=7443)

    def test_check_connected_node(self, tmp_home: Path) -> None:
        """Checking a connected node should report panel connected status."""
        cluster = _configured_cluster()
        panel = _make_panel_mock()
        panel.get_node.return_value = _make_api_node(connected=True)

        mock_conn = MagicMock()
        mock_conn.check_ssh.return_value = None
        mock_conn.run.side_effect = [
            _ssh_result("true\n"),  # container running
            _ssh_result("1\n"),  # port 443
            _ssh_result("notAfter=Dec 31 23:59:59 2026 GMT\n"),  # TLS
            _ssh_result("2048M\n"),  # disk
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
            _ssh_result("true\n"),  # container running
            _ssh_result("1\n"),  # port 443
            _ssh_result("notAfter=Dec 31 23:59:59 2026 GMT\n"),  # TLS
            _ssh_result("2048M\n"),  # disk
        ]

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.ssh.ServerConnection", return_value=mock_conn),
            pytest.raises(typer.Exit) as exc_info,
        ):
            mock_load.return_value = cluster
            run_check(ip_or_name="exit-node")

        assert exc_info.value.exit_code == 4

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
            _ssh_result("true\n"),
            _ssh_result("1\n"),
            _ssh_result("notAfter=Dec 31 23:59:59 2026 GMT\n"),
            _ssh_result("2048M\n"),
        ]

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.ssh.ServerConnection", return_value=mock_conn),
            pytest.raises(typer.Exit) as exc_info,
        ):
            mock_load.return_value = cluster
            run_check(ip_or_name="198.51.100.2")

        assert exc_info.value.exit_code == 3
        # SSH checks still ran
        mock_conn.check_ssh.assert_called_once()


# ---------------------------------------------------------------------------
# TestNodeRemove
# ---------------------------------------------------------------------------


class TestNodeRemove:
    def test_remove_rejects_v4_projected_node(self) -> None:
        cluster = ClusterConfig(topology_intent=_minimal_v4_intent())
        topology = _projected_topology(
            TopologyNode(
                ip="198.51.100.20",
                name="exit-a",
                uuid="exit-a",
                is_panel_host=False,
                ssh_user="root",
                ssh_port=22,
                domain="",
                sni="www.example.com",
                xhttp_path="",
                ws_path="",
            )
        )

        with (
            patch("meridian.commands.node.load_cluster", return_value=cluster),
            patch("meridian.commands.node.topology_from_local_cluster", return_value=topology),
            patch("meridian.commands.node.make_panel") as make_panel,
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_remove(ip_or_name="exit-a", yes=True)

        assert exc_info.value.exit_code == 2
        make_panel.assert_not_called()

    def test_remove_existing_node(self, tmp_home: Path, _patch_cluster_config: Path) -> None:
        """Removing a valid exit node should disable + delete from panel and save cluster."""
        cluster = _configured_cluster()
        panel = _make_panel_mock()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.commands.node.confirm", return_value=True),
            patch("meridian.commands.node._stop_node_containers", return_value=True),
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
            patch("meridian.commands.node._stop_node_containers", return_value=True),
            patch(
                "meridian.operations.remove_relay",
                side_effect=lambda target, _panel, *, relay_ip: target.relays.clear(),
            ),
            patch.object(ClusterConfig, "save"),
        ):
            mock_load.return_value = cluster
            run_remove(ip_or_name="198.51.100.2", force=True)

        # Should have warned about dependent relays
        mock_warn.assert_any_call("Force-removing 1 dependent relay(s) before node removal: relay")
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
            patch("meridian.commands.node._stop_node_containers", return_value=True),
        ):
            mock_load.return_value = cluster
            run_remove(ip_or_name="198.51.100.2")

        # Only the panel node should remain
        assert len(cluster.nodes) == 1
        assert cluster.nodes[0].ip == "198.51.100.1"

    def test_remove_panel_api_error_retains_node(self, tmp_home: Path, _patch_cluster_config: Path) -> None:
        """A failed panel delete retains local state for a retry."""
        cluster = _configured_cluster()
        panel = _make_panel_mock()
        panel.disable_node.side_effect = RemnawaveError("Panel down")
        panel.delete_node.side_effect = RemnawaveError("Panel down")

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.commands.node.confirm", return_value=True),
            patch("meridian.commands.node._stop_node_containers", return_value=True),
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = cluster
            run_remove(ip_or_name="198.51.100.2")

        assert len(cluster.nodes) == 2
        assert cluster.find_node("198.51.100.2") is not None

    def test_remove_reports_remote_change_when_local_state_cannot_be_saved(
        self,
        tmp_home: Path,
        _patch_cluster_config: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        cluster = _configured_cluster()
        panel = _make_panel_mock()

        def remove_then_fail(_cluster: ClusterConfig, remote_panel: MagicMock, **_kwargs: object) -> None:
            remote_panel.delete_node("550e8400-e29b-41d4-a716-446655440002")
            raise OSError("disk full")

        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.operations.remove_node", side_effect=remove_then_fail),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_remove(ip_or_name="198.51.100.2", yes=True)

        assert exc_info.value.exit_code == 3
        panel.delete_node.assert_called_once()
        output = capsys.readouterr().err
        assert "removed remotely" in output
        assert "Remote state changed" in output


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

    def test_list_api_error_preserves_local_rows(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Panel failure leaves configured nodes visible with unknown status."""
        cluster = _configured_cluster()
        panel = _make_panel_mock()
        panel.list_nodes.side_effect = RemnawaveError("Panel unreachable")

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            pytest.raises(typer.Exit) as exc_info,
        ):
            mock_load.return_value = cluster
            run_list()

        assert exc_info.value.exit_code == 3
        output = capsys.readouterr().err
        assert "panel-node" in output
        assert "exit-node" in output
        assert "status unavailable" in output

    def test_list_json_panel_error_emits_partial_envelope(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cluster = _configured_cluster()
        panel = _make_panel_mock()
        panel.list_nodes.side_effect = RemnawaveError("Panel unreachable")

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                pytest.raises(typer.Exit) as exc_info,
            ):
                run_list()
        finally:
            set_json_mode(False)

        assert exc_info.value.exit_code == 3
        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "node.list"
        assert payload["status"] == "ok"
        assert payload["exit_code"] == 3
        assert payload["warnings"][0]["code"] == "MERIDIAN_NODE_STATUS_UNAVAILABLE"
        assert [item["status"] for item in payload["data"]["nodes"]] == ["unknown", "unknown"]

    def test_list_json_outputs_envelope(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """node list --json produces a meridian.output/v1 envelope."""
        cluster = _configured_cluster()
        panel = _make_panel_mock()
        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            ):
                mock_load.return_value = cluster
                run_list()
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["schema"] == "meridian.output/v1"
        assert payload["command"] == "node.list"
        assert payload["status"] == "ok"
        assert payload["summary"]["counts"]["nodes"] == 2
        assert len(payload["data"]["nodes"]) == 2
        assert payload["data"]["nodes"][0]["ip"] == "198.51.100.1"
        assert payload["data"]["nodes"][0]["status"] == "connected"

    def test_list_uses_v4_topology_projection(self, capsys: pytest.CaptureFixture[str]) -> None:
        """V4 exits come from the read-only topology projection, not legacy nodes."""
        from meridian.core.fleet import FleetTopology, TopologyNode, TopologyPanel

        cluster = _configured_cluster()
        cluster.nodes = []
        topology = FleetTopology(
            panel=TopologyPanel(
                url=cluster.panel.url,
                display_url=cluster.panel.display_url,
                server_ip="198.51.100.10",
                ssh_user="root",
                ssh_port=22,
                deployed_with="",
            ),
            nodes=[
                TopologyNode(
                    ip="198.51.100.20",
                    name="exit-v4",
                    uuid="550e8400-e29b-41d4-a716-446655440020",
                    is_panel_host=False,
                    ssh_user="ubuntu",
                    ssh_port=2222,
                    domain="",
                    sni="www.example.com",
                    xhttp_path="",
                    ws_path="",
                )
            ],
        )
        panel = _make_panel_mock()
        panel.list_nodes.return_value = [
            _make_api_node(uuid="550e8400-e29b-41d4-a716-446655440020"),
        ]

        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                patch("meridian.commands.node.topology_from_local_cluster", return_value=topology),
            ):
                run_list()
        finally:
            set_json_mode(False)

        payload = json.loads(capsys.readouterr().out)
        assert payload["summary"]["counts"]["nodes"] == 1
        assert payload["data"]["nodes"][0]["ip"] == "198.51.100.20"
        assert payload["data"]["nodes"][0]["name"] == "exit-v4"

    def test_list_human_escapes_saved_node_markup(self, capsys: pytest.CaptureFixture[str]) -> None:
        cluster = _configured_cluster()
        topology = _projected_topology(
            TopologyNode(
                ip="198.51.100.20",
                name="[red]visible[/red]",
                uuid="550e8400-e29b-41d4-a716-446655440020",
                is_panel_host=False,
                ssh_user="root",
                ssh_port=22,
                domain="",
                sni="www.example.com",
                xhttp_path="",
                ws_path="",
            )
        )
        panel = _make_panel_mock()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.commands.node.topology_from_local_cluster", return_value=topology),
        ):
            run_list()

        assert "[red]visible[/red]" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# TestRenderCheck (remediation display)
# ---------------------------------------------------------------------------


class TestRenderCheck:
    def test_render_check_failed_with_remediation(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Failed checks with remediation should print the hint below the result."""
        result = CheckResult(
            name="container:remnawave-node",
            status="failed",
            detail="remnawave-node exists but is not running",
            remediation="docker start remnawave-node",
        )
        exit_code = _render_check(result, "Container: remnawave-node")

        assert exit_code == 4
        captured = capsys.readouterr().err
        assert "remnawave-node exists but is not running" in captured
        assert "Run: docker start remnawave-node" in captured

    def test_render_check_failed_without_remediation(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Failed checks without remediation should not print a hint line."""
        result = CheckResult(
            name="custom",
            status="failed",
            detail="something broke",
            remediation="",
        )
        exit_code = _render_check(result, "Custom")

        assert exit_code == 4
        captured = capsys.readouterr().err
        assert "something broke" in captured
        assert "Run:" not in captured

    def test_render_check_warning_with_remediation(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Warning checks with remediation should print the hint below."""
        result = CheckResult(
            name="tls_certificate",
            status="warning",
            detail="Could not verify TLS certificate",
            remediation="Check that nginx is running and serving TLS on port 443",
        )
        exit_code = _render_check(result, "TLS cert")

        assert exit_code == 4
        captured = capsys.readouterr().err
        assert "Could not verify TLS certificate" in captured
        assert "Run: Check that nginx is running" in captured

    def test_render_check_passed_ignores_remediation(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Passed checks should not print remediation even if set."""
        result = CheckResult(
            name="disk_space",
            status="passed",
            detail="42000 MB free",
            remediation="should not appear",
        )
        exit_code = _render_check(result, "Disk")

        assert exit_code == 0
        captured = capsys.readouterr().err
        assert "42000 MB free" in captured
        assert "should not appear" not in captured


# ---------------------------------------------------------------------------
# TestNodeRemoveContainerCleanup
# ---------------------------------------------------------------------------


class TestNodeRemoveContainerCleanup:
    def test_remove_stops_containers_via_ssh(self, tmp_home: Path, _patch_cluster_config: Path) -> None:
        """Node remove should SSH into the node and stop containers."""
        cluster = _configured_cluster()
        panel = _make_panel_mock()

        mock_conn = MagicMock()
        mock_conn.check_ssh.return_value = None
        mock_conn.run.return_value = _ssh_result()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.commands.node.confirm", return_value=True),
            patch("meridian.ssh.ServerConnection", return_value=mock_conn) as mock_sc,
        ):
            mock_load.return_value = cluster
            run_remove(ip_or_name="198.51.100.2")

        # SSH connection was created for the node
        mock_sc.assert_called_once_with(ip="198.51.100.2", user="root", port=22)
        mock_conn.check_ssh.assert_called_once()

        # docker compose down was called for the node
        run_calls = [str(c) for c in mock_conn.run.call_args_list]
        assert any(
            "docker compose" in call and "/opt/remnanode/docker-compose.yml" in call and "down" in call
            for call in run_calls
        )
        assert not any("/opt/remnawave-node" in c for c in run_calls)

    def test_remove_retains_state_if_ssh_fails(self, tmp_home: Path, _patch_cluster_config: Path) -> None:
        """If SSH is unreachable, node remove should fail with retry state intact."""
        from meridian.ssh import SSHError

        cluster = _configured_cluster()
        panel = _make_panel_mock()

        mock_conn = MagicMock()
        mock_conn.check_ssh.side_effect = SSHError("Connection refused")

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.commands.node.confirm", return_value=True),
            patch("meridian.ssh.ServerConnection", return_value=mock_conn),
            patch("meridian.commands.node.warn") as mock_warn,
            pytest.raises(typer.Exit),
        ):
            mock_load.return_value = cluster
            run_remove(ip_or_name="198.51.100.2")

        # Warned about SSH failure
        mock_warn.assert_any_call("Could not stop containers on 198.51.100.2 (SSH unreachable)")

        assert len(cluster.nodes) == 2
        assert cluster.find_node("198.51.100.2") is not None

    def test_remove_does_not_stop_panel_containers_for_non_panel_node(
        self, tmp_home: Path, _patch_cluster_config: Path
    ) -> None:
        """Non-panel nodes should only stop the node container, not panel containers."""
        cluster = _configured_cluster()
        panel = _make_panel_mock()

        mock_conn = MagicMock()
        mock_conn.check_ssh.return_value = None
        mock_conn.run.return_value = _ssh_result()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.commands.node.confirm", return_value=True),
            patch("meridian.ssh.ServerConnection", return_value=mock_conn),
        ):
            mock_load.return_value = cluster
            run_remove(ip_or_name="198.51.100.2")

        # Only one docker compose down call (for the node)
        run_calls = [str(c) for c in mock_conn.run.call_args_list]
        assert any("/opt/remnanode/docker-compose.yml" in c for c in run_calls)
        assert not any("'/opt/remnawave/docker-compose.yml'" in c for c in run_calls)
