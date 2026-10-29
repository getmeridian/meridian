"""The deployer's control-service client and the verification adapter `meridian test` reads through."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from meridian.cluster import ClusterConfig
from meridian.connectivity import create_verification_panel, select_active_client, verification_panel_factory
from meridian.control.client import ControlClient, ControlServiceAuthError
from meridian.control.models import ClientEndpoint
from meridian.control.server import ControlServer
from meridian.control.service import ControlService, token_sha256
from meridian.control_verification import ServiceVerificationPanel
from meridian.remnawave import XRAY_JSON_CLIENT_TYPE, RemnawaveAuthError, RemnawaveNetworkError
from meridian.setup.single_server import single_server_intent
from meridian.xray_client import parse_xray_subscription, proxy_outbounds

FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
ADMIN_TOKEN = "admin-token-for-tests-only-0123456789"
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
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    server = ControlServer(("127.0.0.1", 0), service, token_sha256(ADMIN_TOKEN))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


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

    with ServiceVerificationPanel(url, ADMIN_TOKEN, 5) as panel:
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
    with ServiceVerificationPanel(target, token, 2) as panel, pytest.raises(error):
        panel.get_user("alice")


def test_verification_follows_the_fleet_control_backend() -> None:
    cluster = ClusterConfig()
    cluster.topology_intent = single_server_intent(None, server_ref="srv-a")
    assert verification_panel_factory(cluster) is create_verification_panel

    cluster.topology_intent = single_server_intent(None, server_ref="srv-a", control="meridian")
    assert verification_panel_factory(cluster) is ServiceVerificationPanel
