"""Tests for fleet status and cluster recovery commands.

Covers run_status() from fleet.py and run_recover() from recover.py.
All mocks avoid real TCP/HTTPS connections. Uses RFC 5737 IPs (198.51.100.x).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import (
    ClusterConfig,
    DesiredNode,
    DesiredRelay,
    NodeEntry,
    PanelConfig,
    RelayEntry,
)
from meridian.commands.fleet import run_inventory, run_status
from meridian.commands.recover import run_recover
from meridian.remnawave import ConfigProfile, Inbound, Node, RemnawaveAuthError, RemnawaveError, User

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


def _fleet_cluster() -> ClusterConfig:
    return ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/panel", api_token="tok"),
        nodes=[
            NodeEntry(ip="198.51.100.1", uuid=_UUID_A, is_panel_host=True, name="node-1"),
            NodeEntry(ip="198.51.100.2", uuid=_UUID_B, is_panel_host=False, name="node-2"),
        ],
        relays=[
            RelayEntry(ip="198.51.100.3", exit_node_ip="198.51.100.1", port=443, name="relay-1"),
        ],
    )


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
        uuid="u-" + name,
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
    panel.list_nodes.return_value = []
    panel.list_users.return_value = []
    return panel


def _status_patches(cluster: ClusterConfig, panel: MagicMock):
    """Return a combined context manager that patches load_cluster, make_panel, and relay health."""
    return (
        patch("meridian.commands.fleet.load_cluster", return_value=cluster),
        patch("meridian.commands.fleet.make_panel", return_value=panel),
        patch("meridian.commands.fleet._check_relay_health", return_value=True),
        patch("meridian.commands.fleet.is_json_mode", return_value=False),
    )


# ---------------------------------------------------------------------------
# TestFleetStatus
# ---------------------------------------------------------------------------


class TestFleetStatus:
    def test_status_healthy_panel(self) -> None:
        """Panel ping succeeds -- shows healthy status without raising."""
        cluster = _fleet_cluster()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = [
            _api_node(_UUID_A, connected=True),
            _api_node(_UUID_B, connected=True),
        ]
        panel.list_users.return_value = []

        p_load, p_panel, p_relay, p_json = _status_patches(cluster, panel)
        with p_load, p_panel, p_relay, p_json:
            run_status()  # should not raise

        panel.ping.assert_called_once()
        panel.list_nodes.assert_called_once()

    def test_status_unreachable_panel(self) -> None:
        """Panel ping fails -- still completes, shows UNREACHABLE."""
        cluster = _fleet_cluster()
        panel = _make_panel_mock(ping_ok=False)

        p_load, p_panel, p_relay, p_json = _status_patches(cluster, panel)
        with p_load, p_panel, p_relay, p_json:
            run_status()  # should not raise

        panel.ping.assert_called_once()
        # When panel is unreachable, list_nodes should NOT be called
        panel.list_nodes.assert_not_called()

    def test_status_auth_error_exits_user_failure(self) -> None:
        """Authentication failures are not reported as partial healthy status."""
        cluster = _fleet_cluster()
        panel = _make_panel_mock(ping_ok=False)
        panel.ping.side_effect = RemnawaveAuthError(
            "Panel authentication failed",
            hint="Check your API token.",
            category="user",
        )

        with (
            patch("meridian.commands.fleet.load_cluster", return_value=cluster),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.is_json_mode", return_value=False),
        ):
            with pytest.raises(typer.Exit) as exc_info:
                run_status()

        assert exc_info.value.exit_code == 2

    def test_status_mixed_node_states(self) -> None:
        """Nodes in connected, disconnected, and disabled states."""
        cluster = _fleet_cluster()
        # Add a third node to the cluster for "disabled"
        cluster.nodes.append(
            NodeEntry(ip="198.51.100.4", uuid=_UUID_C, name="node-3"),
        )
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = [
            _api_node(_UUID_A, connected=True),
            _api_node(_UUID_B, connected=False, disabled=False),
            _api_node(_UUID_C, connected=False, disabled=True),
        ]
        panel.list_users.return_value = []

        p_load, p_panel, p_relay, p_json = _status_patches(cluster, panel)
        with p_load, p_panel, p_relay, p_json:
            run_status()  # should complete without errors

    def test_status_relay_healthy(self) -> None:
        """Relay health check returns True."""
        cluster = _fleet_cluster()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = []
        panel.list_users.return_value = []

        with (
            patch("meridian.commands.fleet.load_cluster", return_value=cluster),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet._check_relay_health", return_value=True) as mock_health,
            patch("meridian.commands.fleet.is_json_mode", return_value=False),
        ):
            run_status()

        # Relay health checked once for the single relay
        mock_health.assert_called_once_with("198.51.100.3", 443, 3.0)

    def test_status_relay_unreachable(self) -> None:
        """Relay health check returns False -- still completes."""
        cluster = _fleet_cluster()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = []
        panel.list_users.return_value = []

        with (
            patch("meridian.commands.fleet.load_cluster", return_value=cluster),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet._check_relay_health", return_value=False) as mock_health,
            patch("meridian.commands.fleet.is_json_mode", return_value=False),
        ):
            run_status()

        mock_health.assert_called_once_with("198.51.100.3", 443, 3.0)

    def test_status_json_output_structure(self) -> None:
        """JSON mode outputs dict with panel/nodes/relays/users keys."""
        cluster = _fleet_cluster()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = [
            _api_node(_UUID_A, connected=True),
            _api_node(_UUID_B, connected=False),
        ]
        panel.list_users.return_value = [_api_user("alice"), _api_user("bob", "DISABLED")]

        with (
            patch("meridian.commands.fleet.load_cluster", return_value=cluster),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet._check_relay_health", return_value=True),
            patch("meridian.commands.fleet.is_json_mode", return_value=True),
            patch("meridian.commands.fleet.emit_json") as mock_json,
        ):
            run_status()

        mock_json.assert_called_once()
        payload = mock_json.call_args[0][0]
        data = payload.data
        assert payload.schema_version == "meridian.output/v1"
        assert payload.command == "fleet.status"
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
        cluster = _fleet_cluster()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = [
            _api_node(_UUID_A, connected=True),
            _api_node(_UUID_B, connected=False, disabled=False),
        ]
        panel.list_users.return_value = []

        with (
            patch("meridian.commands.fleet.load_cluster", return_value=cluster),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet._check_relay_health", return_value=True),
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
        cluster = ClusterConfig(
            panel=PanelConfig(url="https://198.51.100.1/panel", api_token="tok"),
            nodes=[],
            relays=[],
        )
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = []
        panel.list_users.return_value = []

        p_load, p_panel, p_relay, p_json = _status_patches(cluster, panel)
        with p_load, p_panel, p_relay, p_json:
            run_status()

    def test_status_with_users(self) -> None:
        """Users are fetched and counted when panel is healthy."""
        cluster = _fleet_cluster()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = []
        panel.list_users.return_value = [
            _api_user("alice", "ACTIVE"),
            _api_user("bob", "ACTIVE"),
            _api_user("carol", "DISABLED"),
        ]

        p_load, p_panel, p_relay, p_json = _status_patches(cluster, panel)
        with p_load, p_panel, p_relay, p_json:
            run_status()

        panel.list_users.assert_called_once()

    def test_status_node_unknown_when_not_in_api(self) -> None:
        """Node present in cluster but missing from API is shown as unknown."""
        cluster = _fleet_cluster()
        panel = _make_panel_mock(ping_ok=True)
        # API returns only one of the two cluster nodes
        panel.list_nodes.return_value = [_api_node(_UUID_A, connected=True)]
        panel.list_users.return_value = []

        with (
            patch("meridian.commands.fleet.load_cluster", return_value=cluster),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet._check_relay_health", return_value=True),
            patch("meridian.commands.fleet.is_json_mode", return_value=True),
            patch("meridian.commands.fleet.emit_json") as mock_json,
        ):
            run_status()

        data = mock_json.call_args[0][0].data
        statuses = {n["uuid"]: n["status"] for n in data["nodes"]}
        assert statuses[_UUID_B] == "unknown"

    def test_status_list_nodes_api_error_handled(self) -> None:
        """RemnawaveError from list_nodes is caught gracefully."""
        cluster = _fleet_cluster()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.side_effect = RemnawaveError("API error")
        panel.list_users.return_value = []

        p_load, p_panel, p_relay, p_json = _status_patches(cluster, panel)
        with p_load, p_panel, p_relay, p_json:
            run_status()  # should not raise

    def test_status_list_nodes_unexpected_error_is_bug(self) -> None:
        """Unexpected adapter bugs must not be reported as successful partial status."""
        cluster = _fleet_cluster()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.side_effect = ValueError("bad adapter")
        panel.list_users.return_value = []

        with (
            patch("meridian.commands.fleet.load_cluster", return_value=cluster),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.is_json_mode", return_value=False),
        ):
            with pytest.raises(typer.Exit) as exc_info:
                run_status()

        assert exc_info.value.exit_code == 1

    def test_status_json_preserves_partial_panel_failures(self) -> None:
        """JSON mode reports source availability and warnings for partial API failures."""
        cluster = _fleet_cluster()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.side_effect = RemnawaveError("API error")
        panel.list_users.return_value = [_api_user("alice")]

        with (
            patch("meridian.commands.fleet.load_cluster", return_value=cluster),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet._check_relay_health", return_value=True),
            patch("meridian.commands.fleet.is_json_mode", return_value=True),
            patch("meridian.commands.fleet.emit_json") as mock_json,
        ):
            run_status()

        payload = mock_json.call_args[0][0]
        assert payload.warnings[0].code == "MERIDIAN_PANEL_NODES_UNAVAILABLE"
        assert payload.data["sources"]["nodes"] == "unavailable"
        assert payload.data["sources"]["users"] == "available"
        assert payload.data["nodes"][0]["status"] == "unknown"
        assert payload.data["summary"]["health"] == "unknown"
        assert payload.data["summary"]["needs_attention"] is True

    def test_status_json_preserves_relay_check_failure_as_warning(self) -> None:
        """Relay checker failures degrade relay source without failing panel data."""
        cluster = _fleet_cluster()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = [_api_node(_UUID_A, connected=True)]
        panel.list_users.return_value = [_api_user("alice")]

        with (
            patch("meridian.commands.fleet.load_cluster", return_value=cluster),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet._check_relay_health", side_effect=RuntimeError("probe crashed")),
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
        cluster = _fleet_cluster()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = []
        panel.list_users.return_value = []

        with (
            patch("meridian.commands.fleet.load_cluster", return_value=cluster),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet._check_relay_health", return_value=False),
            patch("meridian.commands.fleet.is_json_mode", return_value=True),
            patch("meridian.commands.fleet.emit_json") as mock_json,
        ):
            run_status()

        data = mock_json.call_args[0][0].data
        assert len(data["relays"]) == 1
        assert data["relays"][0]["healthy"] is False
        assert data["relays"][0]["health"] == "unhealthy"
        assert data["relays"][0]["ip"] == "198.51.100.3"


# ---------------------------------------------------------------------------
# TestFleetInventory
# ---------------------------------------------------------------------------


class TestFleetInventory:
    def test_inventory_json_redacts_panel_token(self) -> None:
        cluster = _fleet_cluster()
        cluster.nodes[0].xhttp_path = "xhttp-a"
        cluster.nodes[0].ws_path = "ws-a"
        cluster.desired_nodes = [DesiredNode(host="198.51.100.1", name="node-1")]
        cluster.desired_relays = [DesiredRelay(host="198.51.100.3", name="relay-1", exit_node="node-1")]
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.return_value = [_api_node(_UUID_A, connected=True)]

        with (
            patch("meridian.commands.fleet.load_cluster", return_value=cluster),
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
        assert data["nodes"][0]["desired"] is True
        assert data["nodes"][0]["protocols"] == ["reality", "xhttp"]
        assert data["relays"][0]["desired"] is True

    def test_inventory_json_reports_ip_mode_xhttp_protocol(self) -> None:
        cluster = _fleet_cluster()
        cluster.nodes[1].domain = ""
        cluster.nodes[1].xhttp_path = "xhttp-b"
        panel = _make_panel_mock(ping_ok=False)

        with (
            patch("meridian.commands.fleet.load_cluster", return_value=cluster),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.is_json_mode", return_value=True),
            patch("meridian.commands.fleet.emit_json") as mock_json,
        ):
            run_inventory()

        data = mock_json.call_args[0][0].data
        node = next(n for n in data["nodes"] if n["ip"] == "198.51.100.2")
        assert node["protocols"] == ["reality", "xhttp"]

    def test_inventory_json_desired_matching_uses_host_not_name(self) -> None:
        cluster = _fleet_cluster()
        cluster.desired_nodes = [DesiredNode(host="198.51.100.9", name="node-1")]
        cluster.desired_relays = [DesiredRelay(host="198.51.100.8", name="relay-1", exit_node="node-1")]
        panel = _make_panel_mock(ping_ok=False)

        with (
            patch("meridian.commands.fleet.load_cluster", return_value=cluster),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.is_json_mode", return_value=True),
            patch("meridian.commands.fleet.emit_json") as mock_json,
        ):
            run_inventory()

        data = mock_json.call_args[0][0].data
        assert data["nodes"][0]["desired"] is False
        assert data["relays"][0]["desired"] is False
        assert data["desired_nodes"][0]["present"] is False
        assert data["desired_relays"][0]["present"] is False
        assert data["summary"]["unapplied_desired_nodes"] == 1
        assert data["summary"]["unapplied_desired_relays"] == 1

    def test_inventory_json_counts_pending_desired_entries(self) -> None:
        cluster = _fleet_cluster()
        cluster.desired_nodes = [DesiredNode(host="198.51.100.9", name="future-node")]
        cluster.desired_relays = [DesiredRelay(host="198.51.100.8", name="future-relay", exit_node="node-1")]
        panel = _make_panel_mock(ping_ok=False)

        with (
            patch("meridian.commands.fleet.load_cluster", return_value=cluster),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.is_json_mode", return_value=True),
            patch("meridian.commands.fleet.emit_json") as mock_json,
        ):
            run_inventory()

        data = mock_json.call_args[0][0].data
        payload = mock_json.call_args[0][0]
        assert payload.status == "ok"
        assert payload.summary.changed is False
        assert data["panel"]["healthy"] is False
        assert data["summary"]["unapplied_desired_nodes"] == 1
        assert data["summary"]["unapplied_desired_relays"] == 1
        assert data["summary"]["pending_desired_resources"] == 2

    def test_inventory_text_handles_panel_api_error(self) -> None:
        cluster = _fleet_cluster()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.side_effect = RemnawaveError("API error")

        with (
            patch("meridian.commands.fleet.load_cluster", return_value=cluster),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.is_json_mode", return_value=False),
        ):
            run_inventory()

    def test_inventory_unexpected_panel_error_is_bug(self) -> None:
        cluster = _fleet_cluster()
        panel = _make_panel_mock(ping_ok=True)
        panel.list_nodes.side_effect = TypeError("adapter shape changed")

        with (
            patch("meridian.commands.fleet.load_cluster", return_value=cluster),
            patch("meridian.commands.fleet.make_panel", return_value=panel),
            patch("meridian.commands.fleet.is_json_mode", return_value=False),
        ):
            with pytest.raises(typer.Exit) as exc_info:
                run_inventory()

        assert exc_info.value.exit_code == 1


# ---------------------------------------------------------------------------
# TestFleetRecover
# ---------------------------------------------------------------------------


class TestFleetRecover:
    def _make_recover_panel(self, *, ping_ok: bool = True, nodes: list[Node] | None = None) -> MagicMock:
        """Create a MeridianPanel mock for recovery."""
        panel = _make_panel_mock(ping_ok=ping_ok)
        if nodes is None:
            nodes = [
                Node(
                    uuid=_UUID_A,
                    name="node-1",
                    address="198.51.100.1",
                    port=62050,
                    is_connected=True,
                ),
            ]
        panel.list_nodes.return_value = nodes
        panel.list_config_profiles.return_value = [
            ConfigProfile(
                uuid="cp-uuid-1",
                name="meridian-default",
                _raw=self._make_config_profile_raw(),
            )
        ]
        panel.list_inbounds.return_value = [
            Inbound(uuid=_UUID_C, tag="vless-reality", type="vless"),
            Inbound(uuid="880e8400-e29b-41d4-a716-446655440003", tag="vless-xhttp", type="vless"),
        ]
        return panel

    def _make_config_profile_raw(self) -> dict:
        """Build a config profile _raw with Reality keys."""
        return {
            "uuid": "cp-uuid-1",
            "name": "meridian-default",
            "config": {
                "inbounds": [
                    {
                        "tag": "vless-reality",
                        "protocol": "vless",
                        "streamSettings": {
                            "network": "tcp",
                            "security": "reality",
                            "realitySettings": {
                                "privateKey": _SAMPLE_PRIVATE_KEY,
                                "publicKey": _SAMPLE_PUBLIC_KEY,
                                "shortIds": [_SAMPLE_SHORT_ID],
                                "serverNames": [_SAMPLE_SNI],
                            },
                        },
                    },
                ],
                "outbounds": [{"tag": "direct", "protocol": "freedom"}],
            },
        }

    def test_recover_from_running_panel(self, tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Recovery from a running panel creates cluster config."""
        import meridian.config as cfg

        monkeypatch.setattr(cfg, "CLUSTER_CONFIG", tmp_home / "cluster.yml")

        panel = self._make_recover_panel()
        with patch("meridian.commands.recover.MeridianPanel", return_value=panel):
            run_recover("https://198.51.100.1/panel", "test-token")

        saved = ClusterConfig.load(tmp_home / "cluster.yml")
        assert saved.version == 2
        assert saved.panel.url == "https://198.51.100.1/panel"
        assert saved.panel.api_token == "test-token"
        assert len(saved.nodes) == 1

    def test_recover_panel_unreachable_fails(self, tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unreachable panel causes fail() -> typer.Exit."""
        import meridian.config as cfg

        monkeypatch.setattr(cfg, "CLUSTER_CONFIG", tmp_home / "cluster.yml")

        panel = self._make_recover_panel(ping_ok=False)
        with (
            patch("meridian.commands.recover.MeridianPanel", return_value=panel),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_recover("https://198.51.100.1/panel", "test-token")

        assert exc_info.value.exit_code != 0

    def test_recover_profile_api_failure_does_not_write_unsafe_state(
        self, tmp_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Recovery stops if existing Reality credentials cannot be read."""
        import meridian.config as cfg

        monkeypatch.setattr(cfg, "CLUSTER_CONFIG", tmp_home / "cluster.yml")
        panel = self._make_recover_panel()
        panel.list_config_profiles.side_effect = RemnawaveError("profile API unavailable")

        with (
            patch("meridian.commands.recover.MeridianPanel", return_value=panel),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_recover("https://198.51.100.1/panel", "test-token")

        assert exc_info.value.exit_code != 0
        assert not (tmp_home / "cluster.yml").exists()

    def test_recover_missing_private_key_does_not_write_unsafe_state(
        self, tmp_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Recovery refuses all-empty key state that redeploy could rotate."""
        import meridian.config as cfg

        monkeypatch.setattr(cfg, "CLUSTER_CONFIG", tmp_home / "cluster.yml")
        panel = self._make_recover_panel()
        reality_settings = panel.list_config_profiles.return_value[0]._raw["config"]["inbounds"][0]["streamSettings"][
            "realitySettings"
        ]
        reality_settings.pop("privateKey")

        with (
            patch("meridian.commands.recover.MeridianPanel", return_value=panel),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_recover("https://198.51.100.1/panel", "test-token")

        assert exc_info.value.exit_code != 0
        assert not (tmp_home / "cluster.yml").exists()

    def test_recover_extracts_reality_keys(self, tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Recovery extracts Reality private key, short ID, and SNI from config profile."""
        import meridian.config as cfg

        monkeypatch.setattr(cfg, "CLUSTER_CONFIG", tmp_home / "cluster.yml")

        panel = self._make_recover_panel()
        with patch("meridian.commands.recover.MeridianPanel", return_value=panel):
            run_recover("https://198.51.100.1/panel", "test-token")

        saved = ClusterConfig.load(tmp_home / "cluster.yml")
        node = saved.nodes[0]
        assert node.reality_private_key == _SAMPLE_PRIVATE_KEY
        assert node.reality_public_key == _SAMPLE_PUBLIC_KEY
        assert node.reality_short_id == _SAMPLE_SHORT_ID
        assert node.sni == _SAMPLE_SNI

    def test_recover_derives_missing_reality_public_key(self, tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Recovery derives the public key instead of rotating Reality keys."""
        import meridian.config as cfg

        monkeypatch.setattr(cfg, "CLUSTER_CONFIG", tmp_home / "cluster.yml")
        panel = self._make_recover_panel()
        reality_settings = panel.list_config_profiles.return_value[0]._raw["config"]["inbounds"][0]["streamSettings"][
            "realitySettings"
        ]
        reality_settings.pop("publicKey")
        conn = MagicMock()
        conn.__enter__.return_value = conn
        conn.run.return_value = MagicMock(returncode=1, stdout="")

        with (
            patch("meridian.commands.recover.MeridianPanel", return_value=panel),
            patch("meridian.ssh.ServerConnection", return_value=conn),
            patch("meridian.xray_config.derive_reality_public_key", return_value=_SAMPLE_PUBLIC_KEY),
        ):
            run_recover("https://198.51.100.1/panel", "test-token")

        saved = ClusterConfig.load(tmp_home / "cluster.yml")
        assert saved.nodes[0].reality_public_key == _SAMPLE_PUBLIC_KEY

    def test_recover_multiple_nodes_first_is_panel_host(self, tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """First node from API is marked as panel host; others are not."""
        import meridian.config as cfg

        monkeypatch.setattr(cfg, "CLUSTER_CONFIG", tmp_home / "cluster.yml")

        nodes = [
            Node(uuid=_UUID_A, name="node-1", address="198.51.100.1", port=62050, is_connected=True),
            Node(uuid=_UUID_B, name="node-2", address="198.51.100.2", port=62050, is_connected=True),
        ]
        panel = self._make_recover_panel(nodes=nodes)
        with patch("meridian.commands.recover.MeridianPanel", return_value=panel):
            run_recover("https://198.51.100.1/panel", "test-token")

        saved = ClusterConfig.load(tmp_home / "cluster.yml")
        assert len(saved.nodes) == 2
        assert saved.nodes[0].is_panel_host is True
        assert saved.nodes[1].is_panel_host is False

    def test_recover_saves_cluster(self, tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Recovery writes cluster.yml to disk."""
        import meridian.config as cfg

        monkeypatch.setattr(cfg, "CLUSTER_CONFIG", tmp_home / "cluster.yml")

        panel = self._make_recover_panel()
        with patch("meridian.commands.recover.MeridianPanel", return_value=panel):
            run_recover("https://198.51.100.1/panel", "test-token")

        assert (tmp_home / "cluster.yml").exists()
        saved = ClusterConfig.load(tmp_home / "cluster.yml")
        assert len(saved.nodes) == 1
        assert saved.panel.url == "https://198.51.100.1/panel"

    def test_recover_caches_inbounds(self, tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Recovery caches inbound UUIDs by tag in cluster config."""
        import meridian.config as cfg

        monkeypatch.setattr(cfg, "CLUSTER_CONFIG", tmp_home / "cluster.yml")

        panel = self._make_recover_panel()
        with patch("meridian.commands.recover.MeridianPanel", return_value=panel):
            run_recover("https://198.51.100.1/panel", "test-token")

        saved = ClusterConfig.load(tmp_home / "cluster.yml")
        assert "reality" in saved.inbounds
        assert "xhttp" in saved.inbounds
        reality_ref = saved.get_inbound("reality")
        assert reality_ref is not None
        assert reality_ref.uuid == _UUID_C
        assert reality_ref.tag == "vless-reality"

    def test_recover_sets_panel_server_ip(self, tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Recovery sets panel.server_ip from first node's address."""
        import meridian.config as cfg

        monkeypatch.setattr(cfg, "CLUSTER_CONFIG", tmp_home / "cluster.yml")

        panel = self._make_recover_panel()
        with patch("meridian.commands.recover.MeridianPanel", return_value=panel):
            run_recover("https://198.51.100.1/panel", "test-token")

        saved = ClusterConfig.load(tmp_home / "cluster.yml")
        assert saved.panel.server_ip == "198.51.100.1"

    def test_recover_normalizes_trailing_slash(self, tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Trailing slash in panel URL is stripped."""
        import meridian.config as cfg

        monkeypatch.setattr(cfg, "CLUSTER_CONFIG", tmp_home / "cluster.yml")

        panel = self._make_recover_panel()
        with patch("meridian.commands.recover.MeridianPanel", return_value=panel):
            run_recover("https://198.51.100.1/panel/", "test-token")

        saved = ClusterConfig.load(tmp_home / "cluster.yml")
        assert saved.panel.url == "https://198.51.100.1/panel"

    def test_recover_empty_url_fails(self) -> None:
        """Empty panel URL triggers fail() -> typer.Exit."""
        with pytest.raises(typer.Exit) as exc_info:
            run_recover("", "test-token")

        assert exc_info.value.exit_code != 0

    def test_recover_empty_token_fails(self) -> None:
        """Empty API token triggers fail() -> typer.Exit."""
        with pytest.raises(typer.Exit) as exc_info:
            run_recover("https://198.51.100.1/panel", "")

        assert exc_info.value.exit_code != 0

    def test_recover_config_profile_metadata(self, tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Recovery stores config profile UUID and name."""
        import meridian.config as cfg

        monkeypatch.setattr(cfg, "CLUSTER_CONFIG", tmp_home / "cluster.yml")

        panel = self._make_recover_panel()
        with patch("meridian.commands.recover.MeridianPanel", return_value=panel):
            run_recover("https://198.51.100.1/panel", "test-token")

        saved = ClusterConfig.load(tmp_home / "cluster.yml")
        assert saved.config_profile_uuid == "cp-uuid-1"
        assert saved.config_profile_name == "meridian-default"
