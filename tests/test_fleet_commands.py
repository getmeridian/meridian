"""Tests for fleet status and inventory commands.

Covers run_status() and run_inventory() from fleet.py.
All mocks avoid real TCP/HTTPS connections. Uses RFC 5737 IPs (198.51.100.x).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.fleet import run_inventory, run_status
from meridian.core.fleet import FleetTopology, TopologyNode, TopologyPanel, TopologyRelay
from meridian.remnawave import Node, RemnawaveAuthError, RemnawaveError, User

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_UUID_A = "550e8400-e29b-41d4-a716-446655440000"
_UUID_B = "660e8400-e29b-41d4-a716-446655440001"
_UUID_C = "770e8400-e29b-41d4-a716-446655440002"

_SAMPLE_PRIVATE_KEY = "WDMnPFb3sIZpJGcNhI8jBh7bSCIhRak-bNWDaeqJe1Y"
_SAMPLE_PUBLIC_KEY = "QfM7gQZ4h_4XfBfNqGShYqzEgXd6xT2c3YhW_F8m9A0"
_SAMPLE_SHORT_ID = "a1b2c3d4"
_SAMPLE_SNI = "www.example.com"


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _node(ip: str, uuid: str, name: str, *, is_panel_host: bool = False) -> TopologyNode:
    return TopologyNode(
        ip=ip,
        name=name,
        uuid=uuid,
        is_panel_host=is_panel_host,
        ssh_user="root",
        ssh_port=22,
        domain="",
        sni=_SAMPLE_SNI,
        xhttp_path="",
        ws_path="",
        protocols=["reality"],
    )


def _fleet_topology() -> FleetTopology:
    return FleetTopology(
        panel=TopologyPanel(
            url="https://198.51.100.1/panel",
            display_url="https://198.51.100.1/panel/",
            server_ip="198.51.100.1",
            ssh_user="root",
            ssh_port=22,
            deployed_with="",
        ),
        nodes=[
            _node("198.51.100.1", _UUID_A, "node-1", is_panel_host=True),
            _node("198.51.100.2", _UUID_B, "node-2"),
        ],
        relays=[
            TopologyRelay(
                ip="198.51.100.3",
                name="relay-1",
                port=443,
                ssh_user="root",
                ssh_port=22,
                exit_node_ip="198.51.100.1",
                sni=_SAMPLE_SNI,
            )
        ],
    )


@contextmanager
def _fleet_source(topology: FleetTopology) -> Iterator[None]:
    """Serve a saved cluster plus its projected topology to the fleet command."""
    cluster = ClusterConfig(panel=PanelConfig(url=topology.panel.url, api_token="tok"))
    with ExitStack() as stack:
        stack.enter_context(patch("meridian.commands.fleet.load_topology_cluster", return_value=cluster))
        stack.enter_context(patch("meridian.commands.fleet.topology_from_local_cluster", return_value=topology))
        yield


def _api_node(uuid: str, *, connected: bool = True, disabled: bool = False, address: str = "") -> Node:
    return Node(
        uuid=uuid,
        name=f"node-{uuid[:4]}",
        address=address or "198.51.100.1",
        port=62050,
        is_connected=connected,
        is_disabled=disabled,
        xray_version="25.12.1" if connected else "",
        traffic_used=1024 * 1024 * 500 if connected else 0,
    )


def _api_user(name: str = "alice", status: str = "ACTIVE") -> User:
    return User(
        id=str(len(name)),
        short_uuid="s-" + name,
        username=name,
        status=status,
    )


def _make_panel_mock(*, ping_ok: bool = True) -> MagicMock:
    """Create a MeridianPanel mock with context-manager support."""
    panel = MagicMock()
    panel.__enter__ = MagicMock(return_value=panel)
    panel.__exit__ = MagicMock(return_value=False)
    panel.ping.return_value = ping_ok
    panel.get_version.return_value = "3.4.5"
    panel.list_nodes.return_value = []
    panel.list_users.return_value = []
    return panel


def _v4_projected_topology() -> FleetTopology:
    return FleetTopology(
        panel=TopologyPanel(
            url="https://198.51.100.1/panel",
            display_url="https://198.51.100.1/panel/",
            server_ip="198.51.100.10",
            ssh_user="root",
            ssh_port=22,
            deployed_with="",
        ),
        nodes=[
            TopologyNode(
                ip="198.51.100.20",
                name="exit-v4",
                uuid=_UUID_A,
                is_panel_host=False,
                ssh_user="ubuntu",
                ssh_port=2222,
                domain="edge.example.com",
                sni="www.example.com",
                xhttp_path="xhttp-secret",
                ws_path="",
            )
        ],
        access_users=["alice"],
        relays=[
            TopologyRelay(
                ip="198.51.100.30",
                name="relay-v4",
                port=8443,
                ssh_user="root",
                ssh_port=22,
                exit_node_ip="198.51.100.20",
                sni="www.example.com",
            )
        ],
    )


def _status_patches(topology: FleetTopology, panel: MagicMock):
    """Return context managers that patch the fleet source, make_panel, and relay health."""
    return (
        _fleet_source(topology),
        patch("meridian.commands.fleet.make_panel", return_value=panel),
        patch("meridian.commands.fleet.tcp_connect", return_value=True),
        patch("meridian.commands.fleet.is_json_mode", return_value=False),
    )


# ---------------------------------------------------------------------------
# TestFleetStatus
# ---------------------------------------------------------------------------


class TestFleetStatus:
    def test_status_healthy_panel(self) -> None:
        """Panel ping succeeds -- shows healthy status without raising."""
        topology = _fleet_topology()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = [
            _api_node(_UUID_A, connected=True),
            _api_node(_UUID_B, connected=True),
        ]
        panel.list_users.return_value = []

        p_load, p_panel, p_relay, p_json = _status_patches(topology, panel)
        with p_load, p_panel, p_relay, p_json:
            exit_code = run_status()

        assert exit_code == 3
        panel.ping.assert_called_once()
        panel.list_nodes.assert_called_once()

    def test_status_unreachable_panel(self) -> None:
        """Panel ping fails -- still completes, shows UNREACHABLE."""
        topology = _fleet_topology()
        panel = _make_panel_mock(ping_ok=False)

        p_load, p_panel, p_relay, p_json = _status_patches(topology, panel)
        with p_load, p_panel, p_relay, p_json:
            exit_code = run_status()

        assert exit_code == 3
        panel.ping.assert_called_once()
        # When panel is unreachable, list_nodes should NOT be called
        panel.list_nodes.assert_not_called()

    def test_status_auth_error_exits_user_failure(self) -> None:
        """Authentication failures are not reported as partial healthy status."""
        topology = _fleet_topology()
        panel = _make_panel_mock(ping_ok=False)
        panel.ping.side_effect = RemnawaveAuthError(
            "Panel authentication failed",
            hint="Check your API token.",
            category="user",
        )

        with (
            _fleet_source(topology),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.is_json_mode", return_value=False),
        ):
            with pytest.raises(typer.Exit) as exc_info:
                run_status()

        assert exc_info.value.exit_code == 2

    def test_status_mixed_node_states(self) -> None:
        """Nodes in connected, disconnected, and disabled states."""
        topology = _fleet_topology()
        # Add a third node to the cluster for "disabled"
        topology.nodes.append(_node("198.51.100.4", _UUID_C, "node-3"))
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = [
            _api_node(_UUID_A, connected=True),
            _api_node(_UUID_B, connected=False, disabled=False),
            _api_node(_UUID_C, connected=False, disabled=True),
        ]
        panel.list_users.return_value = []

        p_load, p_panel, p_relay, p_json = _status_patches(topology, panel)
        with p_load, p_panel, p_relay, p_json:
            exit_code = run_status()

        assert exit_code == 4

    def test_status_relay_healthy(self) -> None:
        """Relay health check returns True."""
        topology = _fleet_topology()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = []
        panel.list_users.return_value = []

        with (
            _fleet_source(topology),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.tcp_connect", return_value=True) as mock_health,
            patch("meridian.commands.fleet.is_json_mode", return_value=False),
        ):
            exit_code = run_status()

        assert exit_code == 3
        # Relay health checked once for the single relay
        mock_health.assert_called_once_with("198.51.100.3", 443, 3.0)

    def test_status_relay_unreachable(self) -> None:
        """Relay health check returns False -- still completes."""
        topology = _fleet_topology()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = []
        panel.list_users.return_value = []

        with (
            _fleet_source(topology),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.tcp_connect", return_value=False) as mock_health,
            patch("meridian.commands.fleet.is_json_mode", return_value=False),
        ):
            exit_code = run_status()

        assert exit_code == 4
        mock_health.assert_called_once_with("198.51.100.3", 443, 3.0)

    def test_status_json_output_structure(self) -> None:
        """JSON mode outputs dict with panel/nodes/relays/users keys."""
        topology = _fleet_topology()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = [
            _api_node(_UUID_A, connected=True),
            _api_node(_UUID_B, connected=False),
        ]
        panel.list_users.return_value = [_api_user("alice"), _api_user("bob", "DISABLED")]

        with (
            _fleet_source(topology),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.tcp_connect", return_value=True),
            patch("meridian.commands.fleet.is_json_mode", return_value=True),
            patch("meridian.commands.fleet.emit_json") as mock_json,
        ):
            exit_code = run_status()

        mock_json.assert_called_once()
        payload = mock_json.call_args[0][0]
        data = payload.data
        assert payload.schema_version == "meridian.output/v1"
        assert payload.command == "fleet.status"
        assert exit_code == 4
        assert payload.exit_code == 4
        assert "panel" in data
        assert "nodes" in data
        assert "relays" in data
        assert "users" not in data
        assert "servers" in data
        assert data["sources"] == {
            "nodes": "available",
            "panel": "available",
            "relays": "available",
            "users": "available",
        }
        assert data["panel"]["healthy"] is True
        assert data["panel"]["url"] == "https://198.51.100.1/"
        assert data["summary"]["health"] == "degraded"
        assert data["summary"]["needs_attention"] is True
        assert len(data["nodes"]) == 2
        assert len(data["relays"]) == 1
        assert data["summary"]["users"] == 2

    def test_status_json_node_statuses(self) -> None:
        """JSON mode reports correct status strings for each node."""
        topology = _fleet_topology()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = [
            _api_node(_UUID_A, connected=True),
            _api_node(_UUID_B, connected=False, disabled=False),
        ]
        panel.list_users.return_value = []

        with (
            _fleet_source(topology),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.tcp_connect", return_value=True),
            patch("meridian.commands.fleet.is_json_mode", return_value=True),
            patch("meridian.commands.fleet.emit_json") as mock_json,
        ):
            run_status()

        data = mock_json.call_args[0][0].data
        statuses = {n["uuid"]: n["status"] for n in data["nodes"]}
        assert statuses[_UUID_A] == "connected"
        assert statuses[_UUID_B] == "disconnected"

    def test_status_no_nodes(self) -> None:
        """Cluster with no nodes still runs without error."""
        topology = _fleet_topology().model_copy(update={"nodes": [], "relays": []})
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = []
        panel.list_users.return_value = []

        p_load, p_panel, p_relay, p_json = _status_patches(topology, panel)
        with p_load, p_panel, p_relay, p_json:
            run_status()

    def test_status_with_users(self) -> None:
        """Users are fetched and counted when panel is healthy."""
        topology = _fleet_topology()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = []
        panel.list_users.return_value = [
            _api_user("alice", "ACTIVE"),
            _api_user("bob", "ACTIVE"),
            _api_user("carol", "DISABLED"),
        ]

        p_load, p_panel, p_relay, p_json = _status_patches(topology, panel)
        with p_load, p_panel, p_relay, p_json:
            run_status()

        panel.list_users.assert_called_once()

    def test_status_node_unknown_when_not_in_api(self) -> None:
        """Node present in cluster but missing from API is shown as unknown."""
        topology = _fleet_topology()
        panel = _make_panel_mock(ping_ok=True)
        # API returns only one of the two cluster nodes
        panel.list_nodes.return_value = [_api_node(_UUID_A, connected=True)]
        panel.list_users.return_value = []

        with (
            _fleet_source(topology),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.tcp_connect", return_value=True),
            patch("meridian.commands.fleet.is_json_mode", return_value=True),
            patch("meridian.commands.fleet.emit_json") as mock_json,
        ):
            run_status()

        data = mock_json.call_args[0][0].data
        statuses = {n["uuid"]: n["status"] for n in data["nodes"]}
        assert statuses[_UUID_B] == "unknown"

    def test_status_list_nodes_api_error_handled(self) -> None:
        """RemnawaveError from list_nodes is caught gracefully."""
        topology = _fleet_topology()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.side_effect = RemnawaveError("API error")
        panel.list_users.return_value = []

        p_load, p_panel, p_relay, p_json = _status_patches(topology, panel)
        with p_load, p_panel, p_relay, p_json:
            run_status()  # should not raise

    def test_status_list_nodes_unexpected_error_is_bug(self) -> None:
        """Unexpected adapter bugs surface instead of being reported as partial status."""
        topology = _fleet_topology()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.side_effect = ValueError("bad adapter")
        panel.list_users.return_value = []

        with (
            _fleet_source(topology),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.is_json_mode", return_value=False),
            pytest.raises(ValueError, match="bad adapter"),
        ):
            run_status()

    def test_status_json_preserves_partial_panel_failures(self) -> None:
        """JSON mode reports source availability and warnings for partial API failures."""
        topology = _fleet_topology()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.side_effect = RemnawaveError("API error")
        panel.list_users.return_value = [_api_user("alice")]

        with (
            _fleet_source(topology),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.tcp_connect", return_value=True),
            patch("meridian.commands.fleet.is_json_mode", return_value=True),
            patch("meridian.commands.fleet.emit_json") as mock_json,
        ):
            exit_code = run_status()

        payload = mock_json.call_args[0][0]
        assert exit_code == 3
        assert payload.exit_code == 3
        assert payload.warnings[0].code == "MERIDIAN_PANEL_NODES_UNAVAILABLE"
        assert payload.data["sources"]["nodes"] == "unavailable"
        assert payload.data["sources"]["users"] == "available"
        assert payload.data["nodes"][0]["status"] == "unknown"
        assert payload.data["summary"]["health"] == "unknown"
        assert payload.data["summary"]["needs_attention"] is True

    def test_status_user_source_failure_returns_unknown_exit(self) -> None:
        topology = _fleet_topology()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = [
            _api_node(_UUID_A, connected=True),
            _api_node(_UUID_B, connected=True),
        ]
        panel.list_users.side_effect = RemnawaveError("users unavailable")

        with (
            _fleet_source(topology),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.tcp_connect", return_value=True),
            patch("meridian.commands.fleet.is_json_mode", return_value=True),
            patch("meridian.commands.fleet.emit_json") as emit,
        ):
            exit_code = run_status()

        payload = emit.call_args[0][0]
        assert exit_code == 3
        assert payload.exit_code == 3
        assert payload.data["sources"]["users"] == "unavailable"
        assert payload.data["summary"]["health"] == "unknown"

    def test_status_json_preserves_relay_check_failure_as_warning(self) -> None:
        """Relay checker failures degrade relay source without failing panel data."""
        topology = _fleet_topology()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = [_api_node(_UUID_A, connected=True)]
        panel.list_users.return_value = [_api_user("alice")]

        with (
            _fleet_source(topology),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.tcp_connect", side_effect=RuntimeError("probe crashed")),
            patch("meridian.commands.fleet.is_json_mode", return_value=True),
            patch("meridian.commands.fleet.emit_json") as mock_json,
        ):
            run_status()

        payload = mock_json.call_args[0][0]
        assert payload.warnings[0].code == "MERIDIAN_RELAYS_UNAVAILABLE"
        assert payload.data["sources"]["relays"] == "unavailable"
        assert payload.data["relays"][0]["health"] == "unknown"
        assert payload.data["summary"]["health"] == "unknown"

    def test_status_json_relay_health(self) -> None:
        """JSON output includes relay health status."""
        topology = _fleet_topology()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = []
        panel.list_users.return_value = []

        with (
            _fleet_source(topology),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.tcp_connect", return_value=False),
            patch("meridian.commands.fleet.is_json_mode", return_value=True),
            patch("meridian.commands.fleet.emit_json") as mock_json,
        ):
            run_status()

        data = mock_json.call_args[0][0].data
        assert len(data["relays"]) == 1
        assert data["relays"][0]["healthy"] is False
        assert data["relays"][0]["health"] == "unhealthy"
        assert data["relays"][0]["ip"] == "198.51.100.3"

    def test_status_uses_v4_topology_projection(self) -> None:
        topology = _v4_projected_topology()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = [_api_node(_UUID_A, connected=True)]
        panel.list_users.return_value = [_api_user("alice"), _api_user("meridian-route-123")]

        with (
            _fleet_source(topology),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch(
                "meridian.commands.fleet._check_relays_health",
                return_value={("198.51.100.30", 8443): True},
            ),
            patch("meridian.commands.fleet.is_json_mode", return_value=True),
            patch("meridian.commands.fleet.emit_json") as mock_json,
        ):
            run_status()

        data = mock_json.call_args[0][0].data
        assert data["summary"]["nodes"] == 1
        assert data["summary"]["relays"] == 1
        assert data["summary"]["users"] == 1
        assert data["nodes"][0]["ip"] == "198.51.100.20"
        assert data["relays"][0]["ip"] == "198.51.100.30"


# ---------------------------------------------------------------------------
# TestFleetInventory
# ---------------------------------------------------------------------------


class TestFleetInventory:
    def test_inventory_json_redacts_panel_token(self) -> None:
        topology = _fleet_topology()
        topology.nodes[0] = topology.nodes[0].model_copy(
            update={"xhttp_path": "xhttp-a", "ws_path": "ws-a", "protocols": ["reality", "xhttp", "hysteria2"]}
        )
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = [_api_node(_UUID_A, connected=True)]

        with (
            _fleet_source(topology),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.is_json_mode", return_value=True),
            patch("meridian.commands.fleet.emit_json") as mock_json,
        ):
            run_inventory()

        payload = mock_json.call_args[0][0]
        data = payload.data
        assert payload.schema_version == "meridian.output/v1"
        assert payload.command == "fleet.inventory"
        assert payload.status == "ok"
        assert "api_token" not in data["panel"]
        assert data["panel"]["url"] == "https://198.51.100.1/"
        assert data["panel"]["subscription_page"]["path"] == ""
        assert data["sources"] == {
            "nodes": "available",
            "panel": "available",
            "relays": "not_requested",
            "users": "not_requested",
        }
        assert data["servers"][0]["roles"] == ["panel", "exit"]
        assert data["nodes"][0]["panel_status"] == "connected"
        assert data["nodes"][0]["desired"] is None
        assert data["nodes"][0]["protocols"] == ["reality", "xhttp", "hysteria2"]
        assert data["relays"][0]["desired"] is None

    def test_inventory_uses_v4_topology_projection(self) -> None:
        topology = _v4_projected_topology()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = [_api_node(_UUID_A, connected=True)]

        with (
            _fleet_source(topology),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.is_json_mode", return_value=True),
            patch("meridian.commands.fleet.emit_json") as mock_json,
        ):
            run_inventory()

        data = mock_json.call_args[0][0].data
        assert data["summary"]["nodes"] == 1
        assert data["summary"]["relays"] == 1
        assert data["nodes"][0]["name"] == "exit-v4"
        assert data["nodes"][0]["panel_status"] == "connected"
        assert data["relays"][0]["name"] == "relay-v4"

    def test_inventory_json_reports_ip_mode_xhttp_protocol(self) -> None:
        topology = _fleet_topology()
        topology.nodes[1] = topology.nodes[1].model_copy(
            update={"xhttp_path": "xhttp-b", "protocols": ["reality", "xhttp", "hysteria2"]}
        )
        panel = _make_panel_mock(ping_ok=False)

        with (
            _fleet_source(topology),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.is_json_mode", return_value=True),
            patch("meridian.commands.fleet.emit_json") as mock_json,
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_inventory()

        assert exc_info.value.exit_code == 3
        data = mock_json.call_args[0][0].data
        node = next(n for n in data["nodes"] if n["ip"] == "198.51.100.2")
        assert node["protocols"] == ["reality", "xhttp", "hysteria2"]

    def test_inventory_text_handles_panel_api_error(self) -> None:
        topology = _fleet_topology()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.side_effect = RemnawaveError("API error")

        with (
            _fleet_source(topology),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.is_json_mode", return_value=False),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_inventory()

        assert exc_info.value.exit_code == 3

    def test_inventory_unexpected_panel_error_is_bug(self) -> None:
        topology = _fleet_topology()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.side_effect = TypeError("adapter shape changed")

        with (
            _fleet_source(topology),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.is_json_mode", return_value=False),
            pytest.raises(TypeError, match="adapter shape changed"),
        ):
            run_inventory()


class TestPanelVersionWarnings:
    @pytest.mark.parametrize(
        ("version", "expected"),
        [
            ("3.4.5", []),
            ("4.0.1", ["newer than the tested 3.x"]),
            ("2.8.0", ["older than the minimum supported 3.4.0"]),
        ],
    )
    def test_status_flags_panels_outside_the_supported_range(self, version: str, expected: list[str]) -> None:
        from meridian.commands.fleet import _panel_version_warnings

        panel = _make_panel_mock()
        panel.get_version.return_value = version
        with patch("meridian.commands.fleet.make_panel", return_value=panel):
            warnings = _panel_version_warnings(MagicMock())

        assert len(warnings) == len(expected)
        for warning, fragment in zip(warnings, expected, strict=True):
            assert fragment in warning.message
            assert warning.code == "MERIDIAN_PANEL_VERSION"

    def test_unreachable_metadata_adds_no_duplicate_warning(self) -> None:
        from meridian.commands.fleet import _panel_version_warnings

        panel = _make_panel_mock()
        panel.get_version.side_effect = RemnawaveError("down")
        with patch("meridian.commands.fleet.make_panel", return_value=panel):
            assert _panel_version_warnings(MagicMock()) == []
