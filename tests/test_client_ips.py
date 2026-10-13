"""Optional client IP forwarding: off keeps every address private, on reaches Xray and the panel."""

from __future__ import annotations

from typing import TypeVar

from meridian.compiler import compile_topology
from meridian.compiler.models import InboundPayload, NginxArtifactPayload, NginxRouteSpec, ResourcePlan
from meridian.core.deploy import DeployRequest
from meridian.provision.nginx_render import render_nginx_server_block
from meridian.reconciler.server_render import render_nginx_artifact
from meridian.setup.single_server import request_intent
from meridian.xray_workload import render_workload_config
from tests.test_xray_workload import _keys, _profile

ADDRESSES = {"srv-a": "198.51.100.10"}
T = TypeVar("T")


def _plan(*, client_ips: bool) -> ResourcePlan:
    request = DeployRequest(ip="198.51.100.10", domain="vpn.example.com", client_ips=client_ips)
    intent = request_intent(None, request, server_ref="srv-a", address="198.51.100.10")
    assert intent.forward_client_ips is client_ips
    return compile_topology(intent)


def _payloads(plan: ResourcePlan, kind: type[T]) -> list[T]:
    return [resource.payload for resource in plan.resources if isinstance(resource.payload, kind)]


def test_client_ips_stay_private_by_default() -> None:
    plan = _plan(client_ips=False)

    assert not any(item.forward_client_ip or item.fallback_strip_port for item in _payloads(plan, NginxArtifactPayload))
    assert not any(item.trust_forwarded_ip for item in _payloads(plan, InboundPayload))


def test_forwarding_marks_every_nginx_hop_and_fronted_inbound() -> None:
    plan = _plan(client_ips=True)

    artifacts = _payloads(plan, NginxArtifactPayload)
    assert artifacts and all(item.forward_client_ip for item in artifacts)
    stream = next(item for item in artifacts if item.layer == "stream")
    # The panel's PROXY-protocol listener takes the control route; the camouflage site gets a stripping hop.
    assert {route.backend_port for route in stream.routes if "" in route.server_names} == {8444}
    assert 19000 <= stream.fallback_strip_port < 20000
    inbounds = {item.protocol: item for item in _payloads(plan, InboundPayload)}
    assert all(inbounds[protocol].trust_forwarded_ip for protocol in ("reality", "xhttp", "wss"))
    # Hysteria2 is direct UDP and already sees the real address.
    assert not inbounds["hysteria2"].trust_forwarded_ip


def test_stream_sends_proxy_header_but_never_to_the_camouflage_site() -> None:
    payload = NginxArtifactPayload(
        server_ref="srv-a",
        listener_port=443,
        layer="stream",
        fallback_server_name="www.example.com",
        forward_client_ip=True,
        fallback_strip_port=19123,
        routes=[
            NginxRouteSpec(
                match="sni", server_names=["www.example.com"], backend_server_ref="srv-a", backend_port=10443
            )
        ],
    )

    rendered = render_nginx_artifact("nginx:srv-a:stream:443", payload, ADDRESSES)

    assert "    proxy_protocol on;\n" in rendered
    assert "upstream meridian_fallback_" in rendered and "server 127.0.0.1:19123;" in rendered
    assert "listen 127.0.0.1:19123 proxy_protocol;" in rendered
    assert "proxy_pass www.example.com:443;" in rendered


def test_tls_server_reads_proxy_header_and_overwrites_forwarded_for() -> None:
    payload = NginxArtifactPayload(
        server_ref="srv-a",
        listener_port=18001,
        layer="http",
        tls_hostname="vpn.example.com",
        forward_client_ip=True,
        routes=[
            NginxRouteSpec(
                match="host_path",
                server_names=["vpn.example.com"],
                path="/x",
                backend_server_ref="srv-a",
                backend_port=20443,
                protocol="xhttp",
            )
        ],
    )

    rendered = render_nginx_artifact("nginx:srv-a:http", payload, ADDRESSES)

    assert "listen 127.0.0.1:18001 ssl proxy_protocol;" in rendered
    assert "real_ip_header proxy_protocol;" in rendered
    assert rendered.count("proxy_set_header X-Forwarded-For $remote_addr;") == 2


def test_inbounds_trust_only_the_hop_in_front_of_them() -> None:
    profile = _profile()
    trusting = profile.model_copy(
        update={"inbounds": [item.model_copy(update={"trust_forwarded_ip": True}) for item in profile.inbounds]}
    )

    plain = render_workload_config(profile, reality_keys=_keys())
    config = render_workload_config(trusting, reality_keys=_keys())

    assert all("sockopt" not in inbound["streamSettings"] for inbound in plain["inbounds"])
    sockopts = {inbound["tag"]: inbound["streamSettings"].get("sockopt") for inbound in config["inbounds"]}
    assert sockopts["meridian-exit-a-reality"] == {"acceptProxyProtocol": True}
    assert sockopts["meridian-exit-a-xhttp"] == {"trustedXForwardedFor": ["X-Forwarded-For"]}
    assert sockopts["meridian-exit-a-wss"] == {"trustedXForwardedFor": ["X-Forwarded-For"]}


def test_panel_server_always_offers_a_proxy_protocol_listener() -> None:
    rendered = render_nginx_server_block(
        host="198.51.100.10",
        nginx_internal_port=8443,
        panel_web_base_path="panel",
        panel_internal_port=3000,
        info_page_path="info",
        extra_locations="",
        root_action="return 404;",
        default_action="return 404;",
        mode_comment="",
        tls_comment="",
    )

    assert "listen 127.0.0.1:8443 ssl;" in rendered
    assert "listen 127.0.0.1:8444 ssl proxy_protocol;" in rendered
    assert "real_ip_header proxy_protocol;" in rendered
