"""`meridian deploy IP` maps onto the same topology model as `meridian setup`."""

from __future__ import annotations

import pytest

from meridian.compiler import compile_topology
from meridian.core.errors import LocalStateError
from meridian.core.topology import AccessIntent, ExitIntent, ProtocolPathIntent
from meridian.setup.single_server import single_server_intent


def test_fresh_ip_deploy_is_a_reality_exit_on_the_control_server() -> None:
    intent = single_server_intent(None, server_ref="srv-a", client_name="alice")

    assert intent.control.server_ref == "srv-a" and intent.control.public_hostname == ""
    (exit_,) = intent.exits
    assert exit_.server_ref == "srv-a" and exit_.geo_block is True
    assert [path.protocol for path in exit_.paths] == ["reality"]
    assert exit_.paths[0].reality_sni == "www.microsoft.com"
    assert intent.access.users == ["alice"]
    compile_topology(intent)


def test_domain_adds_tls_paths_and_opt_in_hysteria2() -> None:
    intent = single_server_intent(None, server_ref="srv-a", domain="vpn.example.com", hysteria2=True)

    paths = {path.protocol: path for path in intent.exits[0].paths}
    assert set(paths) == {"reality", "xhttp", "wss", "hysteria2"}
    assert paths["xhttp"].tls_sni == paths["wss"].host == paths["hysteria2"].tls_sni == "vpn.example.com"
    assert paths["xhttp"].path != paths["wss"].path
    compile_topology(intent)


def test_hysteria2_requires_a_domain() -> None:
    with pytest.raises(LocalStateError, match="Hysteria2 needs a domain"):
        single_server_intent(None, server_ref="srv-a", hysteria2=True)


def test_redeploy_keeps_transport_paths_users_and_other_exits() -> None:
    first = single_server_intent(None, server_ref="srv-a", domain="vpn.example.com", client_name="alice")
    other = ExitIntent(
        id="eu",
        server_ref="srv-b",
        paths=[ProtocolPathIntent(id="eu-reality", protocol="reality", reality_sni="www.microsoft.com")],
    )
    fleet = first.model_copy(update={"exits": [*first.exits, other], "access": AccessIntent(users=["alice", "bob"])})

    again = single_server_intent(fleet, server_ref="srv-a", domain="vpn.example.com", client_name="carol", warp=True)

    before = {path.protocol: path.path for path in first.exits[0].paths}
    after = {path.protocol: path.path for path in again.exits[0].paths}
    assert after == before
    assert again.exits[0].warp is True
    assert again.exits[1] == other
    assert again.access.users == ["alice", "bob", "carol"]


def test_unknown_server_in_an_existing_fleet_is_refused() -> None:
    fleet = single_server_intent(None, server_ref="srv-a")

    with pytest.raises(LocalStateError, match="not an exit"):
        single_server_intent(fleet, server_ref="srv-z")


def test_harden_flag_reaches_every_server_baseline() -> None:
    from meridian.compiler.models import ServerBaselinePayload

    intent = single_server_intent(None, server_ref="srv-a").model_copy(update={"harden": False})
    baselines = [r.payload for r in compile_topology(intent).resources if isinstance(r.payload, ServerBaselinePayload)]

    assert baselines and all(not payload.harden for payload in baselines)
