"""The service's subscription links are every person's connection-page handoff."""

from __future__ import annotations

import base64
from contextlib import nullcontext
from types import SimpleNamespace

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.handoff import client_handoffs, user_handoff
from meridian.remnawave import User
from meridian.subscription_links import protocol_key, subscription_protocol_urls

_LINKS = [
    "vless://u@198.51.100.1:443?security=reality&type=tcp&sni=www.example.com#Direct%20Reality",
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


def test_page_and_subscription_use_one_public_url() -> None:
    cluster = ClusterConfig(panel=PanelConfig(url="https://198.51.100.1/adminsecret", sub_path="pages"))
    links = user_handoff(cluster, _user())
    assert links.page_url == links.subscription_url == "https://198.51.100.1/pages/sub/short1"
    assert "adminsecret" not in links.page_url


def test_missing_subscription_path_has_a_plain_error() -> None:
    links = user_handoff(ClusterConfig(panel=PanelConfig(url="https://198.51.100.1/adminsecret")), _user())
    assert links.page_url == links.subscription_url == ""
    assert links.error == "The subscription path is not configured."


def test_handoff_reads_users_without_publishing_or_saving(monkeypatch) -> None:
    cluster = ClusterConfig(panel=PanelConfig(url="https://198.51.100.1/adminsecret", sub_path="pages"))
    panel = SimpleNamespace(get_user=lambda name: _user() if name == "alice" else None)
    monkeypatch.setattr("meridian.handoff.access_panel", lambda _: nullcontext(panel))
    monkeypatch.setattr(cluster, "save", lambda: (_ for _ in ()).throw(AssertionError("A read must not save")))
    links = client_handoffs(cluster, ["alice", "missing"])
    assert links[0].page_url == links[0].subscription_url != ""
    assert links[1].error == "The person does not exist in the control service."


def test_handoff_greets_the_person_by_the_name_they_typed() -> None:
    from tests.support.topology import single_exit_intent

    intent = single_exit_intent(users=("guest",))
    intent = intent.model_copy(
        update={"access": intent.access.model_copy(update={"display_names": {"guest": "مهمان"}})}
    )
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/adminsecret", sub_path="pages"),
        topology_intent=intent,
    )
    user = User(
        id="1",
        short_uuid="short1",
        vless_uuid="550e8400-e29b-41d4-a716-446655440000",
        username="guest",
        status="ACTIVE",
    )
    assert user_handoff(cluster, user).name == "مهمان"
