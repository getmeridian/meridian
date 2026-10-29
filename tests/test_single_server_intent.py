"""`meridian deploy IP` maps onto the same topology model as `meridian setup`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from meridian.compiler.models import HostPayload, NginxArtifactPayload
from meridian.core.deploy import DeployRequest
from meridian.core.errors import LocalStateError
from meridian.core.topology import AccessIntent, ExitIntent, ProtocolPathIntent
from meridian.setup.single_server import request_intent, single_server_intent
from tests.support.topology import compile_panel_pass


def test_fresh_ip_deploy_is_a_reality_exit_on_the_control_server() -> None:
    intent = single_server_intent(None, server_ref="srv-a", client_name="alice")

    assert intent.control.server_ref == "srv-a" and intent.control.public_hostname == ""
    (exit_,) = intent.exits
    assert exit_.server_ref == "srv-a" and exit_.geo_block is True
    assert [path.protocol for path in exit_.paths] == ["reality"]
    assert exit_.paths[0].reality_sni == "www.google.com"
    assert intent.access.users == ["alice"]
    compile_panel_pass(intent)


def test_domain_adds_tls_paths_and_opt_in_hysteria2() -> None:
    intent = single_server_intent(None, server_ref="srv-a", domain="vpn.example.com", hysteria2=True)

    paths = {path.protocol: path for path in intent.exits[0].paths}
    assert set(paths) == {"reality", "xhttp", "wss", "hysteria2"}
    assert paths["xhttp"].tls_sni == paths["wss"].host == paths["hysteria2"].tls_sni == "vpn.example.com"
    assert paths["xhttp"].path != paths["wss"].path
    compile_panel_pass(intent)


def test_every_invisible_method_is_on_by_default_even_without_a_domain() -> None:
    def protocols(**kwargs: object) -> list[str]:
        intent = single_server_intent(None, server_ref="srv-a", **kwargs)  # type: ignore[arg-type]
        return [path.protocol for path in intent.exits[0].paths]

    # Without a domain, XHTTP and Hysteria2 use the server's IPv4 address (Let's Encrypt IP certificate).
    assert protocols(address="198.51.100.10") == ["reality", "xhttp", "hysteria2"]
    assert protocols(address="198.51.100.10", domain="vpn.example.com") == ["reality", "xhttp", "wss", "hysteria2"]
    assert protocols(address="198.51.100.10", hysteria2=False) == ["reality", "xhttp"]
    # No IPv4 literal and no domain: nothing can carry a certificate, so Reality stands alone.
    assert protocols(address="2001:db8::1") == ["reality"]
    ip_paths = single_server_intent(None, server_ref="srv-a", address="198.51.100.10").exits[0].paths
    assert {path.tls_sni for path in ip_paths if path.protocol != "reality"} == {"198.51.100.10"}


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


def test_fingerprint_reaches_every_tcp_path_and_survives_a_plain_redeploy() -> None:
    first = single_server_intent(
        None, server_ref="srv-a", domain="vpn.example.com", hysteria2=True, fingerprint="firefox"
    )
    again = single_server_intent(first, server_ref="srv-a", domain="vpn.example.com", hysteria2=True)

    for intent in (first, again):
        paths = {path.protocol: path.fingerprint for path in intent.exits[0].paths}
        assert paths == {"reality": "firefox", "xhttp": "firefox", "wss": "firefox", "hysteria2": "chrome"}
    hosts = [
        resource.payload
        for resource in compile_panel_pass(again).resources
        if isinstance(resource.payload, HostPayload) and not resource.payload.is_hidden
    ]
    published = [(host.protocol, host.fingerprint) for host in hosts if not host.xray_json_template_ref]
    # The chosen fingerprint leads; direct TLS paths also get the Chrome copy, CDN and QUIC do not.
    assert sorted(published) == [
        ("hysteria2", ""),
        ("reality", "chrome"),
        ("reality", "firefox"),
        ("wss", "firefox"),
        ("xhttp", "chrome"),
        ("xhttp", "firefox"),
    ]


def test_hysteria2_path_refuses_a_tls_fingerprint() -> None:
    with pytest.raises(ValueError, match="QUIC"):
        ProtocolPathIntent(id="h", protocol="hysteria2", tls_sni="vpn.example.com", fingerprint="firefox")


def test_unknown_server_in_an_existing_fleet_is_refused() -> None:
    fleet = single_server_intent(None, server_ref="srv-a")

    with pytest.raises(LocalStateError, match="not an exit"):
        single_server_intent(fleet, server_ref="srv-z")


def test_harden_flag_reaches_every_server_baseline() -> None:
    from meridian.compiler.models import ServerBaselinePayload

    intent = single_server_intent(None, server_ref="srv-a").model_copy(update={"harden": False})
    baselines = [
        r.payload for r in compile_panel_pass(intent).resources if isinstance(r.payload, ServerBaselinePayload)
    ]

    assert baselines and all(not payload.harden for payload in baselines)


def test_self_steal_on_the_domain_reuses_it_for_reality_and_one_tls_server() -> None:
    request = DeployRequest(ip="198.51.100.10", domain="vpn.example.com", self_steal=True)
    intent = request_intent(None, request, server_ref="srv-a", address="198.51.100.10")

    reality = intent.exits[0].paths[0]
    assert (reality.reality_sni, reality.self_steal) == ("vpn.example.com", True)
    plan = compile_panel_pass(intent)
    servers = [
        resource.payload
        for resource in plan.resources
        if isinstance(resource.payload, NginxArtifactPayload) and resource.payload.layer == "http"
    ]
    assert [server.tls_hostname for server in servers] == ["vpn.example.com"]


def test_self_steal_survives_a_plain_redeploy_and_a_new_sni_replaces_it() -> None:
    first = single_server_intent(None, server_ref="srv-a", sni="www.example.org", self_steal=True)

    kept = single_server_intent(first, server_ref="srv-a")
    assert (kept.exits[0].paths[0].reality_sni, kept.exits[0].paths[0].self_steal) == ("www.example.org", True)
    borrowed = single_server_intent(first, server_ref="srv-a", sni="www.example.com")
    assert borrowed.exits[0].paths[0].self_steal is False


def test_self_steal_request_needs_a_hostname() -> None:
    with pytest.raises(ValidationError, match="own domain"):
        DeployRequest(ip="198.51.100.10", self_steal=True)
    assert DeployRequest(ip="198.51.100.10", domain="vpn.example.com").reality_sni == ""
