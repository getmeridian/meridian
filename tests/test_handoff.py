"""Connection pages mirror the canonical subscription a user's apps receive."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.handoff import protocol_key, publish_connection_page, subscription_protocol_urls
from meridian.remnawave import User
from tests.support.mock_connection import MockConnection

_LINKS = [
    "vless://u@198.51.100.1:443?security=reality&type=tcp&sni=www.microsoft.com#Direct%20Reality",
    "vless://u@203.0.113.9:443?security=reality&type=tcp&sni=relay.example#Via%20relay",
    "vless://u@vpn.example:443?security=tls&type=xhttp&path=%2Fx",
    "hysteria2://u@vpn.example:443?sni=vpn.example#Hysteria2",
]


def _subscription() -> str:
    return base64.b64encode("\n".join(_LINKS).encode()).decode()


def _user() -> User:
    return User(id="1", short_uuid="short1", vless_uuid="vless-1", username="alice", status="ACTIVE")


def test_protocol_keys_follow_link_parameters() -> None:
    assert [protocol_key(link) for link in _LINKS] == ["reality", "reality", "xhttp", "hysteria2"]


def test_every_subscription_link_becomes_a_labelled_page_entry() -> None:
    links = subscription_protocol_urls(_subscription())

    assert [link.url for link in links] == _LINKS
    assert [link.label for link in links][:2] == ["Direct Reality", "Via relay"]
    assert links[2].label == "XHTTP"  # unnamed links fall back to the protocol label
    assert all(link.qr_b64 for link in links)


def test_page_is_uploaded_with_the_public_subscription() -> None:
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/adminsecret", server_ip="198.51.100.1", sub_path="pages")
    )
    panel = SimpleNamespace(fetch_subscription=lambda _short: SimpleNamespace(content=_subscription()))
    conn = MockConnection()

    links = publish_connection_page(cluster, panel, conn, _user())  # type: ignore[arg-type]

    assert links.error == ""
    assert links.page_url == "https://198.51.100.1/pages/vless-1/"
    assert links.subscription_url == "https://198.51.100.1/pages/sub/short1"
    config = json.loads(conn.write_map["/var/www/private/vless-1/config.json"])
    assert [entry["url"] for entry in config["protocols"]] == _LINKS
    assert config["subscription_url"] == links.subscription_url
    assert "adminsecret" not in json.dumps(config)


def test_missing_page_path_reports_instead_of_publishing() -> None:
    cluster = ClusterConfig(panel=PanelConfig(url="https://198.51.100.1/adminsecret", server_ip="198.51.100.1"))
    conn = MockConnection()

    links = publish_connection_page(cluster, SimpleNamespace(), conn, _user())  # type: ignore[arg-type]

    assert links.page_url == "" and links.error
    assert conn.writes == []


def test_page_names_exit_countries_in_subscription_order() -> None:
    """The control server's exit country leads; a second exit in the same country is not repeated."""
    from meridian.setup.editor import add_exit_to_intent
    from meridian.setup.single_server import single_server_intent

    intent = single_server_intent(None, server_ref="srv-a", sni="www.example.com", region="NL")
    for ref, title, region in (("srv-b", "exit-2", "DE"), ("srv-c", "exit-3", "NL")):
        intent = add_exit_to_intent(intent, server_ref=ref, title=title, reality_sni="www.example.com", region=region)
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/adminsecret", server_ip="198.51.100.1", sub_path="pages"),
        topology_intent=intent,
    )
    panel = SimpleNamespace(fetch_subscription=lambda _short: SimpleNamespace(content=_subscription()))
    conn = MockConnection()

    publish_connection_page(cluster, panel, conn, _user())  # type: ignore[arg-type]

    config = json.loads(conn.write_map["/var/www/private/vless-1/config.json"])
    assert config["exit_countries"] == ["NL", "DE"]


def test_page_without_known_countries_omits_them() -> None:
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/adminsecret", server_ip="198.51.100.1", sub_path="pages")
    )
    panel = SimpleNamespace(fetch_subscription=lambda _short: SimpleNamespace(content=_subscription()))
    conn = MockConnection()

    publish_connection_page(cluster, panel, conn, _user())  # type: ignore[arg-type]

    assert "exit_countries" not in json.loads(conn.write_map["/var/www/private/vless-1/config.json"])


def test_published_pages_are_recorded_so_later_reads_show_the_link(tmp_home, monkeypatch) -> None:
    """client add and deploy printed the page link, but the evidence was never saved: client show hid it."""
    from contextlib import nullcontext

    from meridian import handoff
    from meridian.pwa import connection_page_deployed

    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/adminsecret", server_ip="198.51.100.1", sub_path="pages")
    )
    cluster.save()
    panel = SimpleNamespace(
        get_user=lambda _name: _user(),
        fetch_subscription=lambda _short: SimpleNamespace(content=_subscription()),
    )
    monkeypatch.setattr(handoff, "MeridianPanel", lambda *_args: nullcontext(panel))
    monkeypatch.setattr(handoff, "control_connection", lambda _cluster: nullcontext(MockConnection()))

    links = handoff.publish_connection_pages(cluster, ["alice"])

    assert links[0].page_url
    assert connection_page_deployed(ClusterConfig.load(), "vless-1")


def test_page_greets_the_person_by_the_name_they_typed() -> None:
    from tests.support.topology import single_exit_intent

    intent = single_exit_intent(users=("alice", "boba"))
    intent = intent.model_copy(
        update={"access": intent.access.model_copy(update={"display_names": {"alice": "Алиса"}})}
    )
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/adminsecret", server_ip="198.51.100.1", sub_path="pages"),
        topology_intent=intent,
    )
    panel = SimpleNamespace(fetch_subscription=lambda _short: SimpleNamespace(content=_subscription()))
    conn = MockConnection()

    links = publish_connection_page(cluster, panel, conn, _user())  # type: ignore[arg-type]

    assert (links.username, links.name) == ("alice", "Алиса")
    assert json.loads(conn.write_map["/var/www/private/vless-1/config.json"])["client_name"] == "Алиса"
    page = conn.write_map["/var/www/private/vless-1/index.html"]
    assert "Алиса" in (page.decode() if isinstance(page, bytes) else page)
