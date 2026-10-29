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
from tests.support.topology import panel_mock

_UUID_A = "550e8400-e29b-41d4-a716-446655440000"
_UUID_B = "660e8400-e29b-41d4-a716-446655440001"
_UUID_C = "770e8400-e29b-41d4-a716-446655440002"

_SAMPLE_PRIVATE_KEY = "WDMnPFb3sIZpJGcNhI8jBh7bSCIhRak-bNWDaeqJe1Y"
_SAMPLE_PUBLIC_KEY = "QfM7gQZ4h_4XfBfNqGShYqzEgXd6xT2c3YhW_F8m9A0"
_SAMPLE_SHORT_ID = "a1b2c3d4"
_SAMPLE_SNI = "www.example.com"


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
    return panel_mock(ping=ping_ok, get_version="3.4.5", list_nodes=[], list_users=[])


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


def _run(
    command,
    panel: MagicMock,
    *,
    topology: FleetTopology | None = None,
    json: bool = False,
    relay=True,
) -> tuple[int | None, object, MagicMock]:
    """Run a fleet command; return its exit code, the JSON payload (if any), and the relay probe mock."""
    probe = {"side_effect": relay} if isinstance(relay, Exception) else {"return_value": relay}
    with (
        _fleet_source(topology or _fleet_topology()),
        patch("meridian.commands.fleet.make_panel", return_value=panel),
        patch("meridian.commands.fleet.fleet_panel", return_value=panel),
        patch("meridian.commands.fleet.tcp_connect", **probe) as tcp,
        patch("meridian.commands.fleet.is_json_mode", return_value=json),
        patch("meridian.commands.fleet.emit_json") as emit,
    ):
        exit_code = command()
    return exit_code, emit.call_args[0][0] if emit.called else None, tcp


def _panel(*, ping_ok: bool = True, nodes=(), users=()) -> MagicMock:
    panel = _make_panel_mock(ping_ok=ping_ok)
    panel.list_nodes.return_value = list(nodes)
    panel.list_users.return_value = list(users)
    return panel


class TestFleetStatus:
    def test_status_healthy_panel(self) -> None:
        panel = _panel(nodes=[_api_node(_UUID_A), _api_node(_UUID_B)])

        assert _run(run_status, panel)[0] == 3
        panel.ping.assert_called_once()
        panel.list_nodes.assert_called_once()

    def test_status_unreachable_panel_skips_node_listing(self) -> None:
        panel = _panel(ping_ok=False)

        assert _run(run_status, panel)[0] == 3
        panel.ping.assert_called_once()
        panel.list_nodes.assert_not_called()

    def test_status_auth_error_exits_user_failure(self) -> None:
        """Authentication failures are not reported as partial healthy status."""
        panel = _panel(ping_ok=False)
        panel.ping.side_effect = RemnawaveAuthError(
            "Panel authentication failed", hint="Check your API token.", category="user"
        )

        with pytest.raises(typer.Exit) as exc_info:
            _run(run_status, panel)

        assert exc_info.value.exit_code == 2

    def test_status_mixed_node_states(self) -> None:
        topology = _fleet_topology()
        topology.nodes.append(_node("198.51.100.4", _UUID_C, "node-3"))
        panel = _panel(
            nodes=[
                _api_node(_UUID_A, connected=True),
                _api_node(_UUID_B, connected=False, disabled=False),
                _api_node(_UUID_C, connected=False, disabled=True),
            ]
        )

        assert _run(run_status, panel, topology=topology)[0] == 4

    @pytest.mark.parametrize(("reachable", "exit_code"), [(True, 3), (False, 4)])
    def test_status_probes_each_relay(self, reachable: bool, exit_code: int) -> None:
        code, _, probe = _run(run_status, _panel(), relay=reachable)

        assert code == exit_code
        probe.assert_called_once_with("198.51.100.3", 443, 3.0)

    @pytest.mark.parametrize(
        ("empty_topology", "users"),
        [(True, []), (False, [_api_user("alice"), _api_user("bob"), _api_user("carol", "DISABLED")])],
        ids=["no-nodes", "with-users"],
    )
    def test_status_completes_with_sparse_or_populated_panels(self, empty_topology: bool, users: list[User]) -> None:
        topology = _fleet_topology()
        if empty_topology:
            topology = topology.model_copy(update={"nodes": [], "relays": []})
        panel = _panel(users=users)

        _run(run_status, panel, topology=topology)

        panel.list_users.assert_called_once()

    def test_status_list_nodes_api_error_is_partial_status(self) -> None:
        panel = _panel()
        panel.list_nodes.side_effect = RemnawaveError("API error")

        _run(run_status, panel)

    @pytest.mark.parametrize(
        ("command", "error"),
        [(run_status, ValueError("bad adapter")), (run_inventory, TypeError("adapter shape changed"))],
        ids=["status", "inventory"],
    )
    def test_unexpected_adapter_error_is_a_bug_not_partial_status(self, command, error: Exception) -> None:
        panel = _panel()
        panel.list_nodes.side_effect = error

        with pytest.raises(type(error), match=str(error)):
            _run(command, panel)

    def test_status_json_output_structure(self) -> None:
        panel = _panel(
            nodes=[_api_node(_UUID_A, connected=True), _api_node(_UUID_B, connected=False)],
            users=[_api_user("alice"), _api_user("bob", "DISABLED")],
        )

        exit_code, payload, _ = _run(run_status, panel, json=True)

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
        statuses = {n["uuid"]: n["status"] for n in data["nodes"]}
        assert statuses == {_UUID_A: "connected", _UUID_B: "disconnected"}

    def test_status_node_unknown_when_not_in_api(self) -> None:
        _, payload, _ = _run(run_status, _panel(nodes=[_api_node(_UUID_A)]), json=True)

        statuses = {n["uuid"]: n["status"] for n in payload.data["nodes"]}
        assert statuses[_UUID_B] == "unknown"

    def test_status_json_preserves_partial_panel_failures(self) -> None:
        """JSON mode reports source availability and warnings for partial API failures."""
        panel = _panel(users=[_api_user("alice")])
        panel.list_nodes.side_effect = RemnawaveError("API error")

        exit_code, payload, _ = _run(run_status, panel, json=True)

        assert exit_code == 3
        assert payload.exit_code == 3
        assert payload.warnings[0].code == "MERIDIAN_PANEL_NODES_UNAVAILABLE"
        assert payload.data["sources"]["nodes"] == "unavailable"
        assert payload.data["sources"]["users"] == "available"
        assert payload.data["nodes"][0]["status"] == "unknown"
        assert payload.data["summary"]["health"] == "unknown"
        assert payload.data["summary"]["needs_attention"] is True

    def test_status_user_source_failure_returns_unknown_exit(self) -> None:
        panel = _panel(nodes=[_api_node(_UUID_A), _api_node(_UUID_B)])
        panel.list_users.side_effect = RemnawaveError("users unavailable")

        exit_code, payload, _ = _run(run_status, panel, json=True)

        assert exit_code == 3
        assert payload.exit_code == 3
        assert payload.data["sources"]["users"] == "unavailable"
        assert payload.data["summary"]["health"] == "unknown"

    def test_status_json_preserves_relay_check_failure_as_warning(self) -> None:
        """Relay checker failures degrade relay source without failing panel data."""
        panel = _panel(nodes=[_api_node(_UUID_A)], users=[_api_user("alice")])

        _, payload, _ = _run(run_status, panel, json=True, relay=RuntimeError("probe crashed"))

        assert payload.warnings[0].code == "MERIDIAN_RELAYS_UNAVAILABLE"
        assert payload.data["sources"]["relays"] == "unavailable"
        assert payload.data["relays"][0]["health"] == "unknown"
        assert payload.data["summary"]["health"] == "unknown"

    def test_status_json_relay_health(self) -> None:
        _, payload, _ = _run(run_status, _panel(), json=True, relay=False)

        relays = payload.data["relays"]
        assert len(relays) == 1
        assert relays[0]["healthy"] is False
        assert relays[0]["health"] == "unhealthy"
        assert relays[0]["ip"] == "198.51.100.3"

    def test_status_uses_v4_topology_projection(self) -> None:
        panel = _panel(nodes=[_api_node(_UUID_A)], users=[_api_user("alice"), _api_user("meridian-route-123")])

        with patch(
            "meridian.commands.fleet._check_relays_health",
            return_value={("198.51.100.30", 8443): True},
        ):
            _, payload, _ = _run(run_status, panel, topology=_v4_projected_topology(), json=True)

        data = payload.data
        assert data["summary"]["nodes"] == 1
        assert data["summary"]["relays"] == 1
        assert data["summary"]["users"] == 1
        assert data["nodes"][0]["ip"] == "198.51.100.20"
        assert data["relays"][0]["ip"] == "198.51.100.30"


class TestFleetInventory:
    def test_inventory_json_redacts_panel_token(self) -> None:
        topology = _fleet_topology()
        topology.nodes[0] = topology.nodes[0].model_copy(
            update={"xhttp_path": "xhttp-a", "ws_path": "ws-a", "protocols": ["reality", "xhttp", "hysteria2"]}
        )

        _, payload, _ = _run(run_inventory, _panel(nodes=[_api_node(_UUID_A)]), topology=topology, json=True)

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
        assert data["nodes"][0]["protocols"] == ["reality", "xhttp", "hysteria2"]

    def test_inventory_uses_v4_topology_projection(self) -> None:
        panel = _panel(nodes=[_api_node(_UUID_A)])

        _, payload, _ = _run(run_inventory, panel, topology=_v4_projected_topology(), json=True)

        data = payload.data
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

        with (
            _fleet_source(topology),
            patch("meridian.commands.fleet.fleet_panel", return_value=_panel(ping_ok=False)),
            patch("meridian.commands.fleet.is_json_mode", return_value=True),
            patch("meridian.commands.fleet.emit_json") as emit,
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_inventory()

        assert exc_info.value.exit_code == 3
        node = next(n for n in emit.call_args[0][0].data["nodes"] if n["ip"] == "198.51.100.2")
        assert node["protocols"] == ["reality", "xhttp", "hysteria2"]

    def test_inventory_text_handles_panel_api_error(self) -> None:
        panel = _panel()
        panel.list_nodes.side_effect = RemnawaveError("API error")

        with pytest.raises(typer.Exit) as exc_info:
            _run(run_inventory, panel)

        assert exc_info.value.exit_code == 3


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
