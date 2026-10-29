"""Tests for node add/check/list/remove commands.

Nodes are the exits and routing gateways of the saved topology; add applies
a reviewed intent change, check/list read the projected topology.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands._helpers import ReviewedApplyPersistenceError, persist_reviewed_apply
from meridian.commands.node import run_add, run_list
from meridian.console import set_json_mode
from meridian.core.errors import LocalStateError
from meridian.core.fleet import FleetTopology, TopologyNode, TopologyPanel
from meridian.core.topology import SetupIntent
from meridian.remnawave import Node, RemnawaveError
from tests.support.topology import panel_mock, single_exit_intent


def _configured_cluster() -> ClusterConfig:
    """Create a configured cluster whose topology projects a panel node and an exit node."""
    return ClusterConfig(
        panel=PanelConfig(
            url="https://198.51.100.1/panel",
            api_token="tok",
            server_ip="198.51.100.1",
            secret_path="/secret",
        ),
        topology_intent=_minimal_v4_intent(),
    )


def _topology_node(ip: str, uuid: str, name: str, *, is_panel_host: bool = False) -> TopologyNode:
    return TopologyNode(
        ip=ip,
        name=name,
        uuid=uuid,
        is_panel_host=is_panel_host,
        ssh_user="root",
        ssh_port=22,
        domain="",
        sni="www.example.com",
        xhttp_path="",
        ws_path="",
    )


def _minimal_v4_intent() -> SetupIntent:
    return single_exit_intent()


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
    return panel_mock(
        list_nodes=[
            _make_api_node(uuid="550e8400-e29b-41d4-a716-446655440001"),
            _make_api_node(uuid="550e8400-e29b-41d4-a716-446655440002"),
        ],
        get_node=_make_api_node(),
        disable_node=None,
        delete_node=None,
    )


def _ssh_result(stdout: str = "", rc: int = 0) -> subprocess.CompletedProcess[str]:
    """Build a fake SSH CompletedProcess."""
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr="")


@pytest.fixture(autouse=True)
def _projected_fleet() -> object:
    """Project the configured cluster into two nodes with 443 listener evidence."""
    topology = _projected_topology(
        _topology_node("198.51.100.1", "550e8400-e29b-41d4-a716-446655440001", "panel-node", is_panel_host=True),
        _topology_node("198.51.100.2", "550e8400-e29b-41d4-a716-446655440002", "exit-node"),
    )
    deployment = SimpleNamespace(
        kind="v4",
        public_tcp_ports=(443,),
        public_udp_ports=(),
        internal_ports={},
        public_tls_ports=(443,),
        https_routes=(),
    )
    with (
        patch("meridian.commands.node.topology_from_local_cluster", return_value=topology),
        patch("meridian.verification.deployment_verification_context", return_value=deployment),
    ):
        yield


class TestNodeAdd:
    def test_v4_persist_wraps_local_write_errors(self) -> None:
        cluster = ClusterConfig()

        with (
            patch.object(cluster, "save", side_effect=OSError("disk full")),
            pytest.raises(ReviewedApplyPersistenceError, match="disk full") as exc_info,
        ):
            persist_reviewed_apply(cluster)

        assert "remote state may have changed" in str(exc_info.value)

    def test_v4_apply_state_error_is_rendered_at_command_boundary(self) -> None:
        cluster = ClusterConfig(topology_intent=_minimal_v4_intent())
        registry = MagicMock()
        registry.find.return_value = SimpleNamespace(id="srv-new", host="198.51.100.5", name="new-exit")
        resolved = SimpleNamespace(ip="198.51.100.5", user="root", port=22, machine_id="")

        with (
            patch("meridian.commands.node.load_topology_cluster", return_value=cluster),
            patch("meridian.servers.ServerRegistry", return_value=registry),
            patch("meridian.commands.resolve.resolve_server", return_value=resolved),
            patch("meridian.commands.resolve.ensure_server_connection", return_value=resolved),
            patch("meridian.commands.node._neighbor_sni", return_value=""),
            patch(
                "meridian.setup.runtime.SetupRuntime.apply_intent",
                side_effect=LocalStateError("review state changed"),
            ),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_add(ip="198.51.100.5", name="new-exit", yes=True)

        assert exc_info.value.exit_code == 2
        registry.add.assert_called_once()

    def test_rerunning_add_for_a_pending_exit_resumes_instead_of_adding_a_second_exit(self) -> None:
        cluster = ClusterConfig(topology_intent=_minimal_v4_intent())
        pending = cluster.topology_intent.exits[0]
        registry = MagicMock()
        registry.find.return_value = SimpleNamespace(id=pending.server_ref, host="198.51.100.5", name="exit")
        resolved = SimpleNamespace(ip="198.51.100.5", user="root", port=22, machine_id="")
        applied = MagicMock(return_value=SimpleNamespace(all_succeeded=True, changed=True))

        with (
            patch("meridian.commands.node.load_topology_cluster", return_value=cluster),
            patch("meridian.servers.ServerRegistry", return_value=registry),
            patch("meridian.commands.resolve.resolve_server", return_value=resolved),
            patch("meridian.commands.resolve.ensure_server_connection", return_value=resolved),
            patch("meridian.commands.node._neighbor_sni", return_value=""),
            patch("meridian.setup.runtime.SetupRuntime.apply_intent", applied),
        ):
            run_add(ip="198.51.100.5", name="exit", yes=True)

        assert applied.call_args.args[0] == cluster.topology_intent

    def test_rerunning_add_with_a_country_sets_it_on_the_existing_exit(self) -> None:
        cluster = ClusterConfig(topology_intent=_minimal_v4_intent())
        pending = cluster.topology_intent.exits[0]
        registry = MagicMock()
        registry.find.return_value = SimpleNamespace(id=pending.server_ref, host="198.51.100.5", name="exit")
        resolved = SimpleNamespace(ip="198.51.100.5", user="root", port=22, machine_id="")
        applied = MagicMock(return_value=SimpleNamespace(all_succeeded=True, changed=True))

        with (
            patch("meridian.commands.node.load_topology_cluster", return_value=cluster),
            patch("meridian.servers.ServerRegistry", return_value=registry),
            patch("meridian.commands.resolve.resolve_server", return_value=resolved),
            patch("meridian.commands.resolve.ensure_server_connection", return_value=resolved),
            patch("meridian.commands.node._neighbor_sni", return_value=""),
            patch("meridian.setup.runtime.SetupRuntime.apply_intent", applied),
        ):
            run_add(ip="198.51.100.5", country="nl", yes=True)

        applied_intent = applied.call_args.args[0]
        assert applied_intent.exits[0].region == "NL"
        assert applied_intent.exits[0].paths == pending.paths

    def test_new_exit_takes_its_country_from_the_address_registry(self) -> None:
        cluster = ClusterConfig(topology_intent=_minimal_v4_intent())
        registry = MagicMock()
        registry.find.return_value = SimpleNamespace(id="srv-new", host="198.51.100.5", name="new-exit")
        resolved = SimpleNamespace(ip="198.51.100.5", user="root", port=22, machine_id="")
        applied = MagicMock(return_value=SimpleNamespace(all_succeeded=True, changed=True))

        with (
            patch("meridian.commands.node.load_topology_cluster", return_value=cluster),
            patch("meridian.servers.ServerRegistry", return_value=registry),
            patch("meridian.commands.resolve.resolve_server", return_value=resolved),
            patch("meridian.commands.resolve.ensure_server_connection", return_value=resolved),
            patch("meridian.commands.node._neighbor_sni", return_value=""),
            patch("meridian.commands.node.registry_country", return_value="FI") as lookup,
            patch("meridian.setup.runtime.SetupRuntime.apply_intent", applied),
        ):
            run_add(ip="198.51.100.5", name="new-exit", yes=True)

        lookup.assert_called_once_with("198.51.100.5")
        assert applied.call_args.args[0].exits[-1].region == "FI"

    def test_adding_a_saved_server_by_name_keeps_its_title_and_names_the_exit(self) -> None:
        """Without --name the profile was resaved under its IP, so the saved name stopped resolving."""
        cluster = ClusterConfig(topology_intent=_minimal_v4_intent())
        registry = MagicMock()
        registry.find.return_value = SimpleNamespace(id="srv-new", host="198.51.100.5", name="finland")
        resolved = SimpleNamespace(ip="198.51.100.5", user="root", port=22, machine_id="")
        applied = MagicMock(return_value=SimpleNamespace(all_succeeded=True, changed=True))

        with (
            patch("meridian.commands.node.load_topology_cluster", return_value=cluster),
            patch("meridian.servers.ServerRegistry", return_value=registry),
            patch("meridian.commands.resolve.resolve_server", return_value=resolved),
            patch("meridian.commands.resolve.ensure_server_connection", return_value=resolved),
            patch("meridian.commands.node._neighbor_sni", return_value=""),
            patch("meridian.commands.node.registry_country", return_value="FI"),
            patch("meridian.setup.runtime.SetupRuntime.apply_intent", applied),
        ):
            run_add(ip="finland", yes=True)

        assert registry.add.call_args.args[0].name == "finland"
        assert applied.call_args.args[0].exits[-1].id == "exit-finland"

    def test_rerunning_add_with_a_different_sni_refuses_instead_of_ignoring_it(self) -> None:
        cluster = ClusterConfig(topology_intent=_minimal_v4_intent())
        pending = cluster.topology_intent.exits[0]
        registry = MagicMock()
        registry.find.return_value = SimpleNamespace(id=pending.server_ref, host="198.51.100.5", name="exit")
        resolved = SimpleNamespace(ip="198.51.100.5", user="root", port=22, machine_id="")

        with (
            patch("meridian.commands.node.load_topology_cluster", return_value=cluster),
            patch("meridian.servers.ServerRegistry", return_value=registry),
            patch("meridian.commands.resolve.resolve_server", return_value=resolved),
            patch("meridian.commands.resolve.ensure_server_connection", return_value=resolved),
            patch("meridian.commands.node._neighbor_sni", return_value=""),
            patch("meridian.setup.runtime.SetupRuntime.apply_intent") as applied,
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_add(ip="198.51.100.5", sni="www.example.net", yes=True)

        assert exc_info.value.exit_code == 2
        applied.assert_not_called()

    def test_v4_apply_persistence_error_warns_remote_state_may_have_changed(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        cluster = ClusterConfig(topology_intent=_minimal_v4_intent())
        registry = MagicMock()
        registry.find.return_value = SimpleNamespace(id="srv-new", host="198.51.100.5", name="new-exit")
        resolved = SimpleNamespace(ip="198.51.100.5", user="root", port=22, machine_id="")

        with (
            patch("meridian.commands.node.load_topology_cluster", return_value=cluster),
            patch("meridian.servers.ServerRegistry", return_value=registry),
            patch("meridian.commands.resolve.resolve_server", return_value=resolved),
            patch("meridian.commands.resolve.ensure_server_connection", return_value=resolved),
            patch("meridian.commands.node._neighbor_sni", return_value=""),
            patch(
                "meridian.setup.runtime.SetupRuntime.apply_intent",
                side_effect=ReviewedApplyPersistenceError("disk full"),
            ),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_add(ip="198.51.100.5", name="new-exit", yes=True)

        assert exc_info.value.exit_code == 3
        output = capsys.readouterr().err
        assert "remote state may have changed" in output
        assert "meridian plan" in output
        assert "meridian apply" in output


class TestNodeList:
    def test_list_shows_nodes(self, tmp_home: Path) -> None:
        cluster = _configured_cluster()
        panel = _make_panel_mock()

        with (
            patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
            patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
            patch("meridian.control_access.ServiceFleetPanel", return_value=panel),
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
            patch("meridian.control_access.ServiceFleetPanel", return_value=panel),
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
                patch("meridian.control_access.ServiceFleetPanel", return_value=panel),
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
        cluster = _configured_cluster()
        panel = _make_panel_mock()
        set_json_mode(True)
        try:
            with (
                patch("meridian.commands._helpers.ClusterConfig.load") as mock_load,
                patch("meridian.commands._helpers.MeridianPanel", return_value=panel),
                patch("meridian.control_access.ServiceFleetPanel", return_value=panel),
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
                patch("meridian.control_access.ServiceFleetPanel", return_value=panel),
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
            patch("meridian.control_access.ServiceFleetPanel", return_value=panel),
            patch("meridian.commands.node.topology_from_local_cluster", return_value=topology),
        ):
            run_list()

        assert "[red]visible[/red]" in capsys.readouterr().err
