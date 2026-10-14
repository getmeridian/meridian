from __future__ import annotations

import pytest

from meridian.core.topology import (
    ControlPlaneIntent,
    DeliveryIntent,
    ExitIntent,
    ProtocolPathIntent,
    SetupIntent,
)
from meridian.setup.editor import (
    add_access_users_to_intent,
    add_exit_to_intent,
    add_relay_to_intent,
    remove_access_user_from_intent,
    remove_exit_from_intent,
    remove_relays_from_intent,
)
from tests.support.topology import reality_path, single_exit_intent


def test_add_access_users_preserves_existing_order() -> None:
    updated = add_access_users_to_intent(_intent(), ["bob", "carol"])

    assert updated.access.users == ["alice", "bob", "carol"]


def test_add_access_users_rejects_existing_name() -> None:
    with pytest.raises(ValueError, match="already exist"):
        add_access_users_to_intent(_intent(), ["alice"])


def _intent() -> SetupIntent:
    return single_exit_intent(
        control=ControlPlaneIntent(
            server_ref="srv-control",
            public_hostname="panel.example.com",
        ),
        exit_id="exit-primary",
        paths=[reality_path("path-primary")],
        users=["alice"],
        delivery=DeliveryIntent(formats=["xray_json"]),
    )


def test_add_exit_compiles_independent_reality_and_domain_paths() -> None:
    updated = add_exit_to_intent(
        _intent(),
        server_ref="srv-second-exit",
        title="Primary",
        reality_sni="www.example.org",
        tls_hostname="edge.example.com",
    )

    added = updated.exits[-1]
    assert added.id == "exit-primary-2"
    assert added.server_ref == "srv-second-exit"
    assert [path.protocol for path in added.paths] == [
        "reality",
        "xhttp",
        "wss",
        "hysteria2",
    ]
    assert added.paths[1].host == "edge.example.com"
    # Random, not derived from the exit ID: a guessable path lets a prober confirm the proxy.
    assert len(added.paths[1].path) == 16 and "exit" not in added.paths[1].path


def test_add_exit_without_a_domain_offers_xhttp_and_hysteria2_on_its_ip() -> None:
    updated = add_exit_to_intent(
        _intent(),
        server_ref="srv-second-exit",
        title="Primary",
        reality_sni="www.example.org",
        address="198.51.100.20",
    )

    added = updated.exits[-1]
    assert [path.protocol for path in added.paths] == ["reality", "xhttp", "hysteria2"]
    assert {path.tls_sni for path in added.paths[1:]} == {"198.51.100.20"}


def test_add_relay_targets_existing_reality_path() -> None:
    updated = add_relay_to_intent(
        _intent(),
        server_ref="srv-relay",
        title="Domestic edge",
        exit_ref="exit-primary",
        listen_port=8443,
        reality_sni="relay.example.com",
    )

    relay = updated.transparent_relays[0]
    assert relay.id == "relay-domestic-edge"
    assert relay.hop_server_refs == ["srv-relay"]
    assert relay.protocol_path_ref == "path-primary"
    assert relay.listen_port == 8443
    assert relay.reality_sni == "relay.example.com"


def test_add_relay_rejects_unknown_exit() -> None:
    with pytest.raises(ValueError, match="does not exist"):
        add_relay_to_intent(
            _intent(),
            server_ref="srv-relay",
            title="edge",
            exit_ref="missing",
            listen_port=443,
        )


def _intent_with_pool() -> SetupIntent:
    second = ExitIntent(
        id="exit-second",
        server_ref="srv-second",
        paths=[ProtocolPathIntent(id="path-second", protocol="reality", reality_sni="www.example.org")],
    )
    payload = _intent().model_dump(mode="json")
    payload["exits"].append(second.model_dump(mode="json"))
    payload["egress_pools"] = [{"id": "pool", "exit_refs": ["exit-primary", "exit-second"]}]
    payload["default_egress_ref"] = "pool"
    return SetupIntent.model_validate(payload)


def test_remove_exit_drops_it_from_egress_pools() -> None:
    updated = remove_exit_from_intent(_intent_with_pool(), "exit-second")

    assert [exit_.id for exit_ in updated.exits] == ["exit-primary"]
    assert updated.egress_pools[0].exit_refs == ["exit-primary"]


def test_remove_exit_refuses_to_empty_a_pool() -> None:
    payload = remove_exit_from_intent(_intent_with_pool(), "exit-second").model_dump(mode="json")
    payload["exits"].append({**payload["exits"][0], "id": "exit-third", "server_ref": "srv-third"})
    payload["default_egress_ref"] = "exit-third"

    with pytest.raises(ValueError, match="would have no exits left"):
        remove_exit_from_intent(SetupIntent.model_validate(payload), "exit-primary")


def test_remove_exit_surfaces_the_broken_rule_as_plain_text() -> None:
    payload = _intent_with_pool().model_dump(mode="json")
    payload["default_egress_ref"] = "exit-second"

    with pytest.raises(ValueError) as exc_info:
        remove_exit_from_intent(SetupIntent.model_validate(payload), "exit-second")
    assert not str(exc_info.value).startswith("Value error")
    assert "validation error" not in str(exc_info.value)


def test_remove_relays_rejects_unknown_chain() -> None:
    with pytest.raises(ValueError, match="not in the topology: relay-x"):
        remove_relays_from_intent(_intent(), {"relay-x"})


def test_display_names_ride_along_when_users_are_added_and_leave_with_them() -> None:
    intent = add_access_users_to_intent(single_exit_intent(), ["biba", "boba"], {"biba": "Биба", "boba": "Боба"})

    assert intent.access.display_names == {"biba": "Биба", "boba": "Боба"}
    assert remove_access_user_from_intent(intent, "biba").access.display_names == {"boba": "Боба"}
