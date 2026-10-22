"""Self-steal camouflage: Reality falls back to the exit's own nginx and certificate."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from meridian.cluster import RealityKeyBinding
from meridian.compiler import TopologyCompileError, compile_topology
from meridian.compiler.models import (
    CertificatePayload,
    ConfigProfilePayload,
    InboundPayload,
    NginxArtifactPayload,
    ResourcePlan,
)
from meridian.core.topology import (
    ControlPlaneIntent,
    ExitIntent,
    ProtocolPathIntent,
    RoutingGatewayIntent,
    TransparentRelayIntent,
)
from meridian.reconciler.server_render import render_nginx_artifact
from meridian.xray_workload import render_workload_config
from tests.support.topology import single_exit_intent

OWN = "www.example.org"
ADDRESSES = {"srv-control": "198.51.100.10", "srv-exit": "198.51.100.11", "srv-relay": "198.51.100.12"}


def _own_reality(sni: str = OWN) -> ProtocolPathIntent:
    return ProtocolPathIntent(id="reality-a", protocol="reality", reality_sni=sni, self_steal=True)


def _xhttp(hostname: str) -> ProtocolPathIntent:
    return ProtocolPathIntent(id="xhttp-a", protocol="xhttp", tls_sni=hostname, host=hostname, path="xh")


def _payloads(plan: ResourcePlan) -> dict[str, object]:
    return {resource.logical_id: resource.payload for resource in plan.resources}


def _streams(plan: ResourcePlan, server_ref: str = "srv-exit") -> list[NginxArtifactPayload]:
    return [
        payload
        for payload in _payloads(plan).values()
        if isinstance(payload, NginxArtifactPayload) and payload.layer == "stream" and payload.server_ref == server_ref
    ]


def _http(plan: ResourcePlan, hostname: str) -> list[tuple[str, NginxArtifactPayload]]:
    return [
        (logical_id, payload)
        for logical_id, payload in _payloads(plan).items()
        if isinstance(payload, NginxArtifactPayload) and payload.layer == "http" and payload.tls_hostname == hostname
    ]


def _keys() -> RealityKeyBinding:
    return RealityKeyBinding(public_key="public-a", private_key="private-a", short_id="0123456789abcdef")


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"protocol": "reality", "reality_sni": "198.51.100.11"}, "domain name"),
        ({"protocol": "xhttp", "tls_sni": OWN, "host": OWN, "path": "xh"}, "Only Reality"),
    ],
    ids=["ip-address", "not-reality"],
)
def test_self_steal_needs_a_reality_path_with_a_domain(fields: dict[str, str], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        ProtocolPathIntent(id="path-a", self_steal=True, **fields)


def test_borrowed_camouflage_serializes_exactly_as_before() -> None:
    """Unset self-steal fields stay out of the intent and payloads, so existing plan hashes never move."""
    plan = compile_topology(single_exit_intent())
    assert "self_steal" not in single_exit_intent().exits[0].paths[0].model_dump()
    dumped = [resource.payload.model_dump(mode="json") for resource in plan.resources]
    assert not [item for item in dumped if {"reality_dest_port", "reality_dest_proxy", "fallback_port"} & set(item)]


def test_own_hostname_gets_a_certificate_and_a_local_fallback_server() -> None:
    plan = compile_topology(single_exit_intent(paths=[_own_reality()]))
    payloads = _payloads(plan)

    [(decoy_id, decoy)] = _http(plan, OWN)
    assert decoy.routes == []  # nothing but nginx's own 404
    assert not decoy.forward_client_ip
    assert any(isinstance(item, CertificatePayload) and item.hostname == OWN for item in payloads.values())

    inbound = payloads["inbound:exit-a:reality-a"]
    assert isinstance(inbound, InboundPayload)
    assert (inbound.reality_dest_port, inbound.reality_dest_proxy) == (decoy.listener_port, False)

    [stream] = _streams(plan)
    assert (stream.fallback_server_name, stream.fallback_port) == ("", decoy.listener_port)
    assert [route.server_names for route in stream.routes] == [[OWN]]
    stream_id = next(item.logical_id for item in plan.resources if item.payload == stream)
    assert decoy_id in next(item for item in plan.resources if item.logical_id == stream_id).dependencies


def test_shared_domain_routes_to_reality_which_passes_xhttp_on() -> None:
    """One name, one route: the 443 stream sends it to Reality, whose fallback is the XHTTP server."""
    plan = compile_topology(single_exit_intent(paths=[_own_reality(), _xhttp(OWN)]))

    [(_, server)] = _http(plan, OWN)
    assert [route.protocol for route in server.routes] == ["xhttp"]
    inbound = _payloads(plan)["inbound:exit-a:reality-a"]
    assert isinstance(inbound, InboundPayload)
    assert inbound.reality_dest_port == server.listener_port

    [stream] = _streams(plan)
    assert [(route.server_names, route.protocol) for route in stream.routes] == [([OWN], "reality")]
    assert stream.fallback_port == server.listener_port


def test_separate_xhttp_domain_keeps_its_own_route() -> None:
    plan = compile_topology(single_exit_intent(paths=[_own_reality(), _xhttp("cdn.example.org")]))

    [stream] = _streams(plan)
    assert sorted(name for route in stream.routes for name in route.server_names) == ["cdn.example.org", OWN]
    [(_, decoy)] = _http(plan, OWN)
    [(_, cdn)] = _http(plan, "cdn.example.org")
    assert decoy.listener_port != cdn.listener_port
    assert stream.fallback_port == decoy.listener_port


def test_forwarded_client_ips_reach_the_fallback_through_reality() -> None:
    """The fallback server reads PROXY headers once the fleet forwards IPs, so Reality must send one."""
    plan = compile_topology(single_exit_intent(paths=[_own_reality()], forward_client_ips=True))
    payloads = _payloads(plan)

    [(_, decoy)] = _http(plan, OWN)
    assert decoy.forward_client_ip
    inbound = payloads["inbound:exit-a:reality-a"]
    assert isinstance(inbound, InboundPayload)
    assert (inbound.trust_forwarded_ip, inbound.reality_dest_proxy) == (True, True)
    profile = payloads["profile:exit-a"]
    assert isinstance(profile, ConfigProfilePayload)
    reality = render_workload_config(profile, reality_keys=_keys())["inbounds"][0]["streamSettings"]["realitySettings"]
    assert reality["dest"] == f"127.0.0.1:{decoy.listener_port}"
    assert reality["xver"] == 1
    [stream] = _streams(plan)
    assert stream.fallback_strip_port == 0  # the local server reads the header itself


def test_rendered_reality_and_stream_point_at_the_local_server() -> None:
    plan = compile_topology(single_exit_intent(paths=[_own_reality()]))
    payloads = _payloads(plan)
    [(decoy_id, decoy)] = _http(plan, OWN)
    profile = payloads["profile:exit-a"]
    assert isinstance(profile, ConfigProfilePayload)

    reality = render_workload_config(profile, reality_keys=_keys())["inbounds"][0]["streamSettings"]["realitySettings"]
    assert (reality["dest"], reality["serverNames"]) == (f"127.0.0.1:{decoy.listener_port}", [OWN])
    assert "xver" not in reality

    [stream] = _streams(plan)
    stream_id = next(item.logical_id for item in plan.resources if item.payload == stream)
    rendered = render_nginx_artifact(stream_id, stream, ADDRESSES)
    assert f"server 127.0.0.1:{decoy.listener_port};" in rendered
    assert "default meridian_fallback_" in rendered
    assert OWN + ":443" not in rendered  # never dials out to itself

    server = render_nginx_artifact(decoy_id, decoy, ADDRESSES)
    assert f"listen 127.0.0.1:{decoy.listener_port} ssl;" in server
    assert f"server_name {OWN};" in server
    assert "location / { return 404; }" in server


def test_relay_on_the_exit_name_falls_back_locally_too() -> None:
    intent = single_exit_intent(
        paths=[_own_reality()],
        transparent_relays=[
            TransparentRelayIntent(
                id="relay-a", hop_server_refs=["srv-relay"], exit_ref="exit-a", protocol_path_ref="reality-a"
            )
        ],
    )
    plan = compile_topology(intent)
    [(_, decoy)] = _http(plan, OWN)
    ingress = [stream for stream in _streams(plan) if stream.listener_port != 443]
    assert [(item.fallback_server_name, item.fallback_port) for item in ingress] == [("", decoy.listener_port)]


def test_relay_with_its_own_name_keeps_borrowing() -> None:
    intent = single_exit_intent(
        paths=[_own_reality()],
        transparent_relays=[
            TransparentRelayIntent(
                id="relay-a",
                hop_server_refs=["srv-relay"],
                exit_ref="exit-a",
                protocol_path_ref="reality-a",
                reality_sni="relay.example.com",
            )
        ],
    )
    plan = compile_topology(intent)
    ingress = [stream for stream in _streams(plan) if stream.listener_port != 443]
    assert [(item.fallback_server_name, item.fallback_port) for item in ingress] == [("relay.example.com", 0)]


def test_gateway_bridge_falls_back_to_the_exit_server() -> None:
    intent = single_exit_intent(
        paths=[_own_reality()],
        routing_gateways=[RoutingGatewayIntent(id="gw-a", server_ref="srv-gateway", bridge_path_ref="reality-a")],
    )
    plan = compile_topology(intent)
    [(_, decoy)] = _http(plan, OWN)
    bridges = [
        payload
        for payload in _payloads(plan).values()
        if isinstance(payload, InboundPayload) and payload.purpose == "bridge"
    ]
    assert [bridge.reality_dest_port for bridge in bridges] == [decoy.listener_port]


def test_panel_domain_cannot_double_as_self_steal_on_the_control_server() -> None:
    intent = single_exit_intent(
        control=ControlPlaneIntent(server_ref="srv-exit", public_hostname=OWN),
        paths=[_own_reality()],
    )
    with pytest.raises(TopologyCompileError, match="panel domain"):
        compile_topology(intent)


def test_two_self_steal_exits_get_independent_servers() -> None:
    intent = single_exit_intent(paths=[_own_reality()])
    second = ExitIntent(id="exit-b", server_ref="srv-exit-b", paths=[_own_reality("de.example.org")])
    plan = compile_topology(intent.model_copy(update={"exits": [*intent.exits, second]}))
    assert len(_http(plan, OWN)) == len(_http(plan, "de.example.org")) == 1
    assert _streams(plan, "srv-exit-b")[0].fallback_port == _http(plan, "de.example.org")[0][1].listener_port
