"""The deployer's control-service client and the access adapter client commands, pages, and tests use."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from meridian.adapters.cluster import topology_from_cluster
from meridian.cluster import ClusterConfig
from meridian.connectivity import create_verification_panel, select_active_client, verification_panel_factory
from meridian.control.client import ControlClient, ControlServiceAuthError, ControlServiceError
from meridian.control.models import ClientEndpoint, NodeSpec
from meridian.control_access import ServiceAccessPanel, ServiceFleetPanel, access_panel, fleet_panel
from meridian.remnawave import (
    XRAY_JSON_CLIENT_TYPE,
    MeridianPanel,
    RemnawaveAuthError,
    RemnawaveError,
    RemnawaveNetworkError,
)
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.editor import add_relay_to_intent
from meridian.setup.single_server import single_server_intent
from meridian.xray_client import parse_xray_subscription, proxy_outbounds
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.topology import GATEWAY_SERVERS, gateway_fleet_intent

REALITY = ClientEndpoint(
    remark="Reality",
    protocol="reality",
    address="198.51.100.7",
    port=443,
    position=0,
    fingerprint="chrome",
    reality_sni="www.example.com",
    reality_public_key="PBK",
    reality_short_id="abcd",
)


@pytest.fixture
def url(tmp_path: Path) -> Iterator[str]:
    with serve_control(tmp_path) as base:
        yield base


def test_client_round_trips_users_and_subscriptions(url: str) -> None:
    with ControlClient(url, ADMIN_TOKEN) as client:
        assert client.get_user("alice") is None
        user = client.put_user("alice")
        client.put_endpoint("main:main-reality:direct", REALITY)

        assert client.get_endpoint("main:main-reality:direct") == REALITY
        assert [item.username for item in client.list_users()] == ["alice"]
        config = parse_xray_subscription(client.fetch_subscription(user.sub_token, document="json"))
    assert [item["tag"] for item in proxy_outbounds(config)] == ["MERIDIAN_PROXY"]


def test_client_refuses_a_wrong_admin_token(url: str) -> None:
    with ControlClient(url, "not-the-token") as client, pytest.raises(ControlServiceAuthError):
        client.health()


def test_verification_panel_selects_service_users_like_panel_users(url: str) -> None:
    with ControlClient(url, ADMIN_TOKEN) as client:
        record = client.put_user("alice")
        client.put_endpoint("main:main-reality:direct", REALITY)
    cluster = ClusterConfig()
    cluster.topology_intent = single_server_intent(None, server_ref="srv-a", client_name="alice", control="meridian")

    with ServiceAccessPanel(url, ADMIN_TOKEN, 5) as panel:
        user = select_active_client(panel, cluster, "")
        assert user is not None
        assert (user.username, user.short_uuid, user.status) == ("alice", record.sub_token, "ACTIVE")
        document = panel.fetch_subscription(user.short_uuid, client_type=XRAY_JSON_CLIENT_TYPE)
    assert proxy_outbounds(parse_xray_subscription(document.content))


@pytest.mark.parametrize(
    ("token", "port", "error"),
    [(ADMIN_TOKEN, 1, RemnawaveNetworkError), ("not-the-token", None, RemnawaveAuthError)],
    ids=["unreachable", "wrong-token"],
)
def test_verification_panel_raises_the_errors_verification_classifies(
    url: str, token: str, port: int | None, error: type[Exception]
) -> None:
    target = f"http://127.0.0.1:{port}" if port else url
    with ServiceAccessPanel(target, token, 2) as panel, pytest.raises(error):
        panel.get_user("alice")


def test_verification_follows_the_fleet_control_backend() -> None:
    cluster = ClusterConfig()
    cluster.topology_intent = single_server_intent(None, server_ref="srv-a")
    assert verification_panel_factory(cluster) is create_verification_panel

    cluster.topology_intent = single_server_intent(None, server_ref="srv-a", control="meridian")
    assert verification_panel_factory(cluster) is ServiceAccessPanel


def test_access_panel_disables_enables_and_reissues_by_panel_id(url: str) -> None:
    with ControlClient(url, ADMIN_TOKEN) as client:
        before = client.put_user("alice")

    with ServiceAccessPanel(url, ADMIN_TOKEN) as panel:
        user = panel.get_user("alice")
        assert user is not None
        panel.disable_user(user.id)
        assert panel.get_user("alice").status == "DISABLED"  # type: ignore[union-attr]
        panel.enable_user(user.id)
        reissued = panel.revoke_user_subscription(user.id)

    assert reissued.status == "ACTIVE"
    assert (reissued.vless_uuid, reissued.short_uuid) != (before.uuid, before.sub_token)


def test_access_panel_refuses_an_id_it_never_read(url: str) -> None:
    with ServiceAccessPanel(url, ADMIN_TOKEN) as panel, pytest.raises(RemnawaveError):
        panel.disable_user("u0000000000000000")


def test_access_panel_follows_the_fleet_control_backend() -> None:
    cluster = ClusterConfig()
    cluster.panel.url = "https://198.51.100.10/secret"
    cluster.panel.api_token = "token"
    cluster.topology_intent = single_server_intent(None, server_ref="srv-a")
    assert isinstance(access_panel(cluster), MeridianPanel)

    cluster.topology_intent = single_server_intent(None, server_ref="srv-a", control="meridian")
    assert isinstance(access_panel(cluster), ServiceAccessPanel)


def test_fleet_panel_lists_only_registered_nodes_and_published_relays(url: str) -> None:
    with ControlClient(url, ADMIN_TOKEN) as client:
        client.put_node("main", NodeSpec.model_validate({"resources": []}))
        client.put_endpoint("relay-1:main-reality", REALITY)

    with ServiceFleetPanel(
        url, ADMIN_TOKEN, node_ids=["main", "gone"], endpoint_ids=["relay-1:main-reality", "relay-2:main-reality"]
    ) as panel:
        assert panel.ping()
        nodes = panel.list_nodes()
        hosts = panel.list_hosts()

    # Registered but never polled: listed, and not connected.
    assert [(node.uuid, node.is_connected) for node in nodes] == [("main", False)]
    assert [(host.uuid, host.is_disabled) for host in hosts] == [("relay-1:main-reality", False)]


def test_fleet_projection_names_service_nodes_and_relays_by_their_service_ids(servers_file: Path) -> None:
    registry = ServerRegistry(servers_file)
    registry.add(ServerEntry(host="198.51.100.10", name="exit-a"))
    registry.add(ServerEntry(host="198.51.100.20", name="relay-a"))
    intent = single_server_intent(None, server_ref=registry.find("exit-a").id, control="meridian")  # type: ignore[union-attr]
    intent = add_relay_to_intent(
        intent,
        server_ref=registry.find("relay-a").id,
        title="relay-a",
        exit_ref="main",
        listen_port=8443,  # type: ignore[union-attr]
    )
    cluster = ClusterConfig(topology_intent=intent)
    cluster.panel.url = "https://198.51.100.10/secret"

    topology = topology_from_cluster(cluster, servers=registry.list())
    panel = fleet_panel(cluster, topology)

    assert [node.uuid for node in topology.nodes] == ["main"]
    relay_ids = [ref.uuid for ref in topology.relays[0].host_refs]
    assert len(relay_ids) == 1 and relay_ids[0].startswith(f"{topology.relays[0].name}:")
    assert isinstance(panel, ServiceFleetPanel)
    cluster.topology_intent = single_server_intent(None, server_ref="srv-a")
    assert isinstance(fleet_panel(cluster, topology), MeridianPanel)


def test_fleet_projection_names_a_service_gateway_by_its_node_id(servers_file: Path) -> None:
    """Status matched the gateway's empty panel UUID, so a running gateway read as unknown."""
    registry = ServerRegistry(servers_file)
    for ref, host in GATEWAY_SERVERS.items():
        registry.add(ServerEntry(host=host, user="root", id=ref))
    cluster = ClusterConfig(topology_intent=gateway_fleet_intent())

    topology = topology_from_cluster(cluster, servers=registry.list())

    assert {(node.name, node.uuid, node.role) for node in topology.nodes} == {
        ("main", "main", "exit"),
        ("de", "de", "exit"),
        ("gw", "gw", "routing_gateway"),
    }


@pytest.mark.parametrize("status", [502, 503, 504])
def test_a_proxy_answering_for_a_stopped_service_is_unreachable_not_a_refusal(status: int) -> None:
    client = ControlClient("https://198.51.100.10/secret", ADMIN_TOKEN)
    client._client = httpx.Client(
        base_url="https://198.51.100.10/secret/", transport=httpx.MockTransport(lambda _request: httpx.Response(status))
    )

    with client, pytest.raises(ControlServiceError) as error:
        client.health()

    assert (error.value.retryable, error.value.category) == (True, "system")
    assert "did not answer" in str(error.value)
