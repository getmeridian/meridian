"""Tests for meridian-core fleet inventory model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from meridian.adapters.cluster import topology_from_cluster, topology_from_local_cluster
from meridian.cluster import (
    ClusterConfig,
    DesiredNode,
    DesiredRelay,
    NodeEntry,
    PanelConfig,
    RelayEntry,
    SubscriptionPageConfig,
    WorkloadBinding,
)
from meridian.core.errors import LocalStateError
from meridian.core.fleet import (
    FleetSources,
    build_fleet_inventory,
    build_fleet_status,
    build_node_list_result,
    build_relay_list_result,
)
from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    ExitIntent,
    ProtocolPathIntent,
    RoutingGatewayIntent,
    SetupIntent,
    TransparentRelayIntent,
)
from meridian.servers import ServerEntry, ServerRegistry


@dataclass
class ApiNode:
    uuid: str
    is_connected: bool = True
    is_disabled: bool = False
    xray_version: str = "25.12.1"
    traffic_used: int = 1024


@dataclass
class ApiUser:
    username: str
    status: str = "ACTIVE"


def _v4_cluster() -> ClusterConfig:
    return ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.10/panel", api_token="secret-token"),
        topology_intent=SetupIntent(
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
                        ),
                        ProtocolPathIntent(
                            id="xhttp-a",
                            protocol="xhttp",
                            tls_sni="edge.example.com",
                            host="edge.example.com",
                            path="xhttp-secret",
                        ),
                        ProtocolPathIntent(
                            id="hy2-a",
                            protocol="hysteria2",
                            tls_sni="edge.example.com",
                        ),
                    ],
                )
            ],
            transparent_relays=[
                TransparentRelayIntent(
                    id="relay-a",
                    hop_server_refs=["srv-relay-a", "srv-relay-b"],
                    exit_ref="exit-a",
                    protocol_path_ref="reality-a",
                    listen_port=8443,
                )
            ],
            routing_gateways=[
                RoutingGatewayIntent(
                    id="gateway-a",
                    server_ref="srv-gateway",
                    bridge_path_ref="reality-a",
                )
            ],
            default_egress_ref="exit-a",
            access=AccessIntent(users=["default"]),
        ),
        workloads=[
            WorkloadBinding(
                id="exit-a",
                generation=2,
                active=True,
                node_uuids={"srv-exit": "node-exit-a"},
                host_uuids={"host:relay-a:reality-a": "host-relay-a"},
            ),
            WorkloadBinding(
                id="gateway-a",
                kind="routing_gateway",
                generation=2,
                active=True,
                node_uuids={"srv-gateway": "node-gateway-a"},
            ),
        ],
        active_generation=2,
    )


def _v4_servers() -> list[ServerEntry]:
    return [
        ServerEntry(id="srv-control", host="198.51.100.10", name="control"),
        ServerEntry(id="srv-exit", host="198.51.100.20", name="exit-server", user="ubuntu", port=2222),
        ServerEntry(id="srv-relay-a", host="198.51.100.30", name="relay-entry"),
        ServerEntry(id="srv-relay-b", host="198.51.100.31", name="relay-internal"),
        ServerEntry(id="srv-gateway", host="198.51.100.40", name="routing-gateway"),
    ]


def test_v4_projection_exposes_all_xray_nodes_and_advertised_relay_endpoint() -> None:
    topology = topology_from_cluster(_v4_cluster(), servers=_v4_servers())
    inventory = build_fleet_inventory(topology, panel_healthy=True)

    assert topology.panel.server_ip == "198.51.100.10"
    assert [node.name for node in topology.nodes] == ["exit-a", "gateway-a"]
    assert topology.nodes[0].model_dump() == {
        "ip": "198.51.100.20",
        "name": "exit-a",
        "uuid": "node-exit-a",
        "is_panel_host": False,
        "ssh_user": "ubuntu",
        "ssh_port": 2222,
        "domain": "edge.example.com",
        "sni": "www.example.com",
        "xhttp_path": "xhttp-secret",
        "ws_path": "",
        "role": "exit",
        "protocols": ["reality", "xhttp", "hysteria2"],
    }
    assert topology.nodes[1].model_dump() == {
        "ip": "198.51.100.40",
        "name": "gateway-a",
        "uuid": "node-gateway-a",
        "is_panel_host": False,
        "ssh_user": "root",
        "ssh_port": 22,
        "domain": "",
        "sni": "www.example.com",
        "xhttp_path": "",
        "ws_path": "",
        "role": "routing_gateway",
        "protocols": ["reality"],
    }
    assert [relay.model_dump() for relay in topology.relays] == [
        {
            "ip": "198.51.100.30",
            "name": "relay-a",
            "port": 8443,
            "ssh_user": "root",
            "ssh_port": 22,
            "exit_node_ip": "198.51.100.20",
            "sni": "www.example.com",
            "host_refs": [{"protocol": "reality", "uuid": "host-relay-a"}],
        }
    ]
    assert inventory.summary.nodes == 2
    assert inventory.summary.relays == 1
    assert inventory.nodes[0].protocols == ["reality", "xhttp", "hysteria2"]
    assert inventory.nodes[1].role == "routing_gateway"
    assert {server.ip for server in inventory.servers} == {
        "198.51.100.10",
        "198.51.100.20",
        "198.51.100.30",
        "198.51.100.40",
        "198.51.100.31",
    }
    internal_hop = next(server for server in inventory.servers if server.id == "srv-relay-b")
    assert internal_hop.roles == ["relay"]
    assert internal_hop.relay_hops[0].model_dump() == {
        "chain": "relay-a",
        "position": 2,
        "advertised": False,
        "health": "unknown",
    }

    status = build_fleet_status(
        topology,
        panel_healthy=True,
        api_nodes=[
            ApiNode(uuid="node-exit-a"),
            ApiNode(uuid="node-gateway-a"),
        ],
        api_users=[ApiUser(username="default")],
        relay_health={("198.51.100.30", 8443): True},
        sources=FleetSources(panel="available", nodes="available", users="available", relays="available"),
    )
    assert status.summary.health == "unknown"
    assert status.summary.unknown_relay_hops == 1


def test_v4_projection_fails_when_a_referenced_server_profile_is_missing() -> None:
    servers = [server for server in _v4_servers() if server.id != "srv-relay-b"]

    with pytest.raises(LocalStateError, match="srv-relay-b"):
        topology_from_cluster(_v4_cluster(), servers=servers)


def test_v4_local_projection_reads_saved_server_registry(tmp_home: Path) -> None:
    registry = ServerRegistry(tmp_home / "servers.json")
    for server in _v4_servers():
        registry.add(server)

    topology = topology_from_local_cluster(_v4_cluster())

    assert topology.nodes[0].ip == "198.51.100.20"
    assert topology.relays[0].ip == "198.51.100.30"


def test_inventory_is_redacted_and_role_protocol_aware() -> None:
    cluster = ClusterConfig(
        panel=PanelConfig(
            url="https://198.51.100.1/secret-panel",
            api_token="secret-token",
            server_ip="198.51.100.1",
        ),
        nodes=[
            NodeEntry(
                ip="198.51.100.1",
                uuid="node-a",
                name="exit-a",
                is_panel_host=True,
                xhttp_path="xhttp-a",
            )
        ],
        relays=[RelayEntry(ip="198.51.100.2", name="relay-a", exit_node_ip="198.51.100.1")],
        subscription_page=SubscriptionPageConfig(enabled=True, path="secret-subscription-path"),
        desired_nodes=[DesiredNode(host="198.51.100.1", name="exit-a")],
        desired_relays=[DesiredRelay(host="198.51.100.2", name="relay-a", exit_node="exit-a")],
    )

    inventory = build_fleet_inventory(
        topology_from_cluster(cluster), panel_healthy=True, api_nodes=[ApiNode(uuid="node-a")]
    )
    data = inventory.to_data()

    assert "api_token" not in data["panel"]
    assert data["panel"]["url"] == "https://198.51.100.1/"
    assert data["panel"]["subscription_page"] == {"enabled": True, "path": ""}
    assert data["panel"]["healthy"] is True
    assert data["sources"] == {"nodes": "unknown", "panel": "unknown", "relays": "unknown", "users": "unknown"}
    assert data["servers"][0]["roles"] == ["panel", "exit"]
    assert data["nodes"][0]["protocols"] == ["reality", "xhttp", "hysteria2"]
    assert data["nodes"][0]["panel_status"] == "connected"
    assert data["relays"][0]["exit_node_name"] == "exit-a"
    assert data["summary"]["unapplied_desired_nodes"] == 0
    assert data["summary"]["unapplied_desired_relays"] == 0


def test_cluster_adapter_preserves_relay_host_protocol_refs() -> None:
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/panel", api_token="secret-token"),
        relays=[
            RelayEntry(
                ip="198.51.100.2",
                name="relay-a",
                host_uuids={"reality": "host-reality", "wss": "host-wss"},
            )
        ],
    )

    topology = topology_from_cluster(cluster)

    assert [ref.model_dump() for ref in topology.relays[0].host_refs] == [
        {"protocol": "reality", "uuid": "host-reality"},
        {"protocol": "wss", "uuid": "host-wss"},
    ]


def test_server_inventory_includes_panel_only_host() -> None:
    cluster = ClusterConfig(
        panel=PanelConfig(
            url="https://198.51.100.1/panel",
            api_token="secret-token",
            server_ip="198.51.100.1",
        ),
        nodes=[],
        relays=[],
    )

    inventory = build_fleet_inventory(topology_from_cluster(cluster), panel_healthy=True)
    data = inventory.to_data()

    assert data["servers"] == [
        {
            "id": "198.51.100.1",
            "ip": "198.51.100.1",
            "name": "",
            "roles": ["panel"],
            "ssh_port": 22,
            "ssh_user": "root",
            "relay_hops": [],
        }
    ]


def test_inventory_desired_matching_uses_host_identity() -> None:
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/panel", api_token="secret-token"),
        nodes=[NodeEntry(ip="198.51.100.1", uuid="node-a", name="reused-name")],
        relays=[RelayEntry(ip="198.51.100.2", name="reused-relay")],
        desired_nodes=[DesiredNode(host="198.51.100.9", name="reused-name")],
        desired_relays=[DesiredRelay(host="198.51.100.8", name="reused-relay", exit_node="reused-name")],
    )

    inventory = build_fleet_inventory(topology_from_cluster(cluster), panel_healthy=False)
    data = inventory.to_data()

    assert data["nodes"][0]["desired"] is False
    assert data["relays"][0]["desired"] is False
    assert data["desired_nodes"][0]["present"] is False
    assert data["desired_relays"][0]["present"] is False
    assert data["summary"]["unapplied_desired_nodes"] == 1
    assert data["summary"]["unapplied_desired_relays"] == 1
    assert data["summary"]["pending_desired_resources"] == 2


def test_status_summarizes_nodes_relays_and_users() -> None:
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/panel", api_token="secret-token"),
        nodes=[
            NodeEntry(ip="198.51.100.1", uuid="node-a", name="exit-a", is_panel_host=True),
            NodeEntry(ip="198.51.100.2", uuid="node-b", name="exit-b"),
        ],
        relays=[RelayEntry(ip="198.51.100.3", name="relay-a", exit_node_ip="198.51.100.1")],
    )

    status = build_fleet_status(
        topology_from_cluster(cluster),
        panel_healthy=True,
        api_nodes=[ApiNode(uuid="node-a", is_connected=True)],
        api_users=[ApiUser(username="alice"), ApiUser(username="bob", status="DISABLED")],
        relay_health={("198.51.100.3", 443): False},
    )
    data = status.to_data()

    assert data["panel"] == {"healthy": True, "url": "https://198.51.100.1/"}
    assert data["nodes"][0]["status"] == "connected"
    assert data["nodes"][0]["traffic_bytes"] == 1024
    assert data["nodes"][1]["status"] == "unknown"
    assert data["relays"][0]["exit_node_name"] == "exit-a"
    assert data["relays"][0]["health"] == "unhealthy"
    assert data["relays"][0]["healthy"] is False
    assert data["summary"]["health"] == "degraded"
    assert data["summary"]["needs_attention"] is True
    assert data["summary"]["active_users"] == 1
    assert data["summary"]["disabled_users"] == 1
    assert data["summary"]["unhealthy_relays"] == 1
    assert data["summary"]["unknown_nodes"] == 1


def test_reachable_relay_listener_does_not_certify_route_health() -> None:
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/panel", api_token="secret-token"),
        nodes=[NodeEntry(ip="198.51.100.1", uuid="node-a", name="exit-a", is_panel_host=True)],
        relays=[RelayEntry(ip="198.51.100.3", name="relay-a", exit_node_ip="198.51.100.1")],
    )

    status = build_fleet_status(
        topology_from_cluster(cluster),
        panel_healthy=True,
        api_nodes=[ApiNode(uuid="node-a", is_connected=True)],
        api_users=[ApiUser(username="alice")],
        relay_health={("198.51.100.3", 443): True},
        sources=FleetSources(panel="available", nodes="available", users="available", relays="available"),
    )

    assert status.relays[0].healthy is True
    assert status.relays[0].health == "unknown"
    assert status.summary.unknown_relays == 1
    assert status.summary.health == "unknown"


@pytest.mark.parametrize(
    ("users", "missing", "nonactive"),
    [([], 1, 0), ([ApiUser(username="alice", status="DISABLED")], 0, 1)],
)
def test_v4_declared_access_user_defects_degrade_fleet(
    users: list[ApiUser],
    missing: int,
    nonactive: int,
) -> None:
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/panel", api_token="secret-token"),
        nodes=[NodeEntry(ip="198.51.100.1", uuid="node-a", name="exit-a", is_panel_host=True)],
    )
    topology = topology_from_cluster(cluster).model_copy(update={"access_users": ["alice"]})

    status = build_fleet_status(
        topology,
        panel_healthy=True,
        api_nodes=[ApiNode(uuid="node-a", is_connected=True)],
        api_users=users,
        sources=FleetSources(panel="available", nodes="available", users="available", relays="not_requested"),
    )

    assert status.summary.missing_access_users == missing
    assert status.summary.nonactive_access_users == nonactive
    assert status.summary.health == "degraded"


def test_status_marks_health_unknown_when_required_node_source_unavailable() -> None:
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/panel", api_token="secret-token"),
        nodes=[NodeEntry(ip="198.51.100.1", uuid="node-a", name="exit-a", is_panel_host=True)],
    )

    status = build_fleet_status(
        topology_from_cluster(cluster),
        panel_healthy=True,
        api_nodes=[],
        api_users=[],
        relay_health={},
        sources=FleetSources(panel="available", nodes="unavailable", users="available", relays="not_requested"),
    )
    data = status.to_data()

    assert data["summary"]["health"] == "unknown"
    assert data["summary"]["needs_attention"] is True
    assert data["summary"]["unknown_nodes"] == 1


def test_status_marks_health_unknown_when_user_source_unavailable() -> None:
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/panel", api_token="secret-token"),
        nodes=[NodeEntry(ip="198.51.100.1", uuid="node-a", name="exit-a", is_panel_host=True)],
    )

    status = build_fleet_status(
        topology_from_cluster(cluster),
        panel_healthy=True,
        api_nodes=[ApiNode(uuid="node-a", is_connected=True)],
        api_users=[],
        relay_health={},
        sources=FleetSources(panel="available", nodes="available", users="unavailable", relays="not_requested"),
    )

    assert status.summary.health == "unknown"
    assert status.summary.needs_attention is True


def test_status_treats_disabled_node_as_degraded_even_if_connected() -> None:
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/panel", api_token="secret-token"),
        nodes=[NodeEntry(ip="198.51.100.1", uuid="node-a", name="exit-a", is_panel_host=True)],
    )

    status = build_fleet_status(
        topology_from_cluster(cluster),
        panel_healthy=True,
        api_nodes=[ApiNode(uuid="node-a", is_connected=True, is_disabled=True)],
        api_users=[],
        relay_health={},
        sources=FleetSources(panel="available", nodes="available", users="available", relays="not_requested"),
    )

    assert status.nodes[0].status == "disabled"
    assert status.summary.health == "degraded"
    assert status.summary.disabled_nodes == 1


def test_node_and_relay_list_results_preserve_command_wire_shapes() -> None:
    topology = topology_from_cluster(
        ClusterConfig(
            nodes=[NodeEntry(ip="198.51.100.1", uuid="node-a", name="exit-a", is_panel_host=True)],
            relays=[
                RelayEntry(
                    ip="198.51.100.2",
                    name="relay-a",
                    exit_node_ip="198.51.100.1",
                    port=8443,
                    sni="www.example.com",
                )
            ],
        )
    )

    nodes = build_node_list_result(topology.nodes, [ApiNode(uuid="node-a")])
    relays = build_relay_list_result(topology.relays, {("198.51.100.2", 8443): False})

    assert nodes.to_data() == {
        "nodes": [
            {
                "ip": "198.51.100.1",
                "name": "exit-a",
                "uuid": "node-a",
                "is_panel_host": True,
                "role": "exit",
                "status": "connected",
                "xray_version": "25.12.1",
                "traffic_bytes": 1024,
            }
        ]
    }
    assert relays.to_data() == {
        "relays": [
            {
                "ip": "198.51.100.2",
                "name": "relay-a",
                "exit_node_ip": "198.51.100.1",
                "port": 8443,
                "sni": "www.example.com",
                "enabled": False,
            }
        ]
    }
