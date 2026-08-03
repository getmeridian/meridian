from __future__ import annotations

import pytest

from meridian.core.topology import (
    AccessIntent,
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
)


def test_add_access_users_preserves_existing_order() -> None:
    updated = add_access_users_to_intent(_intent(), ["bob", "carol"])

    assert updated.access.users == ["alice", "bob", "carol"]


def test_add_access_users_rejects_existing_name() -> None:
    with pytest.raises(ValueError, match="already exist"):
        add_access_users_to_intent(_intent(), ["alice"])


def _intent() -> SetupIntent:
    return SetupIntent(
        control=ControlPlaneIntent(
            server_ref="srv-control",
            public_hostname="panel.example.com",
        ),
        exits=[
            ExitIntent(
                id="exit-primary",
                server_ref="srv-exit",
                paths=[
                    ProtocolPathIntent(
                        id="path-primary",
                        protocol="reality",
                        reality_sni="www.example.com",
                    )
                ],
            )
        ],
        default_egress_ref="exit-primary",
        access=AccessIntent(users=["alice"]),
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
    assert added.paths[1].path == "exit-primary-2-xhttp"


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
