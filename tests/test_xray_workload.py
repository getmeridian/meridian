"""V4 workload rendering keeps protocol and secret state isolated."""

from __future__ import annotations

import pytest

from meridian.cluster import RealityKeyBinding
from meridian.compiler.models import (
    ConfigProfilePayload,
    EgressBalancerSpec,
    InboundPayload,
    ServiceOutboundSpec,
    WorkloadRouteSpec,
)
from meridian.xray_workload import (
    ServiceRouteCredential,
    WorkloadConfigError,
    render_workload_config,
)

_EDGE_A_TAG = "meridian-edge-0dbff829e6d1bdd5"
_EDGE_B_TAG = "meridian-edge-c123917cc6e24846"


def _profile(*, warp: bool = False) -> ConfigProfilePayload:
    inbounds = [
        InboundPayload(
            workload_ref="exit-a",
            protocol="reality",
            tag="meridian-exit-a-reality",
            listen_port=10443,
            public_port=443,
            reality_sni="www.microsoft.com",
            reality_server_names=["www.microsoft.com", "relay.example.com"],
        ),
        InboundPayload(
            workload_ref="exit-a",
            protocol="xhttp",
            tag="meridian-exit-a-xhttp",
            listen_port=30443,
            public_port=443,
            tls_sni="vpn-a.example.com",
            host="vpn-a.example.com",
            path="xhttp-a",
        ),
        InboundPayload(
            workload_ref="exit-a",
            protocol="wss",
            tag="meridian-exit-a-wss",
            listen_port=20443,
            public_port=443,
            tls_sni="vpn-a.example.com",
            host="vpn-a.example.com",
            path="ws-a",
        ),
        InboundPayload(
            workload_ref="exit-a",
            protocol="hysteria2",
            tag="meridian-exit-a-hysteria2",
            listen_port=443,
            public_port=443,
            tls_sni="vpn-a.example.com",
        ),
    ]
    return ConfigProfilePayload(
        workload_id="exit-a",
        name="Meridian v4 exit-a",
        inbound_refs=[
            "inbound:exit-a:reality",
            "inbound:exit-a:xhttp",
            "inbound:exit-a:wss",
            "inbound:exit-a:hysteria2",
        ],
        inbounds=inbounds,
        outbound_tags=["warp"] if warp else ["direct"],
    )


def _keys() -> RealityKeyBinding:
    return RealityKeyBinding(
        public_key="public-a",
        private_key="private-a",
        short_id="0123456789abcdef",
    )


def test_renders_exact_reviewed_protocols_and_paths() -> None:
    config = render_workload_config(_profile(), reality_keys=_keys())

    assert [inbound["tag"] for inbound in config["inbounds"]] == [
        "meridian-exit-a-reality",
        "meridian-exit-a-xhttp",
        "meridian-exit-a-wss",
        "meridian-exit-a-hysteria2",
    ]
    reality = config["inbounds"][0]
    assert reality["streamSettings"]["realitySettings"] == {
        "dest": "www.microsoft.com:443",
        "serverNames": ["www.microsoft.com", "relay.example.com"],
        "privateKey": "private-a",
        "shortIds": ["0123456789abcdef"],
        "fingerprint": "chrome",
    }
    assert config["inbounds"][1]["streamSettings"]["xhttpSettings"]["path"] == "/xhttp-a"
    assert config["inbounds"][2]["streamSettings"]["wsSettings"]["path"] == "/ws-a"
    assert config["inbounds"][3]["streamSettings"]["tlsSettings"]["alpn"] == ["h3"]
    assert [outbound["tag"] for outbound in config["outbounds"]] == ["direct", "block"]


def test_warp_is_preferred_without_removing_direct_or_block() -> None:
    config = render_workload_config(_profile(warp=True), reality_keys=_keys())
    assert [outbound["tag"] for outbound in config["outbounds"]] == [
        "warp",
        "direct",
        "block",
    ]
    assert config["outbounds"][0]["protocol"] == "socks"


def test_gateway_renders_ordered_service_routes_and_fail_closed_balancer() -> None:
    profile = ConfigProfilePayload(
        workload_id="gateway-a",
        workload_kind="routing_gateway",
        name="Meridian v4 gateway-a",
        inbound_refs=["inbound:gateway-a:entry"],
        inbounds=[
            InboundPayload(
                workload_ref="gateway-a",
                protocol="reality",
                tag="meridian-gateway-a-reality",
                listen_port=11443,
                public_port=443,
                reality_sni="www.microsoft.com",
                reality_server_names=["www.microsoft.com"],
            )
        ],
        service_outbounds=[
            ServiceOutboundSpec(
                edge_id="gateway-a:exit-a",
                tag=_EDGE_A_TAG,
                service_user_ref="service-user:gateway-a:exit-a",
                target_workload_ref="exit-a",
                target_server_ref="srv-exit-a",
                target_inbound_ref="inbound:exit-a:bridge:gateway-a",
                target_port=41001,
                target_sni="www.microsoft.com",
            ),
            ServiceOutboundSpec(
                edge_id="gateway-a:exit-b",
                tag=_EDGE_B_TAG,
                service_user_ref="service-user:gateway-a:exit-b",
                target_workload_ref="exit-b",
                target_server_ref="srv-exit-b",
                target_inbound_ref="inbound:exit-b:bridge:gateway-a",
                target_port=41002,
                target_sni="www.cloudflare.com",
            ),
        ],
        egress_balancers=[
            EgressBalancerSpec(
                pool_id="primary",
                tag="meridian-pool-gateway-a-primary",
                outbound_tags=[_EDGE_A_TAG, _EDGE_B_TAG],
                strategy="least_ping",
                probe_url="https://www.apple.com/library/test/success.html",
            )
        ],
        routing_rules=[
            WorkloadRouteSpec(
                route_id="regional",
                priority=10,
                match="country",
                match_values=["DE"],
                target_type="outbound",
                target_tag=_EDGE_A_TAG,
            ),
            WorkloadRouteSpec(
                route_id="default",
                priority=11,
                match="all",
                match_values=[],
                target_type="balancer",
                target_tag="meridian-pool-gateway-a-primary",
            ),
        ],
    )
    credentials = {
        "gateway-a:exit-a": ServiceRouteCredential(
            address="198.51.100.10",
            vless_uuid="11111111-1111-4111-8111-111111111111",
            public_key="public-a",
            short_id="aaaaaaaaaaaaaaaa",
        ),
        "gateway-a:exit-b": ServiceRouteCredential(
            address="198.51.100.20",
            vless_uuid="22222222-2222-4222-8222-222222222222",
            public_key="public-b",
            short_id="bbbbbbbbbbbbbbbb",
        ),
    }

    config = render_workload_config(
        profile,
        reality_keys=_keys(),
        service_credentials=credentials,
    )

    assert [outbound["tag"] for outbound in config["outbounds"]] == [
        _EDGE_A_TAG,
        _EDGE_B_TAG,
        "direct",
        "block",
    ]
    assert config["outbounds"][0]["settings"]["vnext"][0]["users"][0]["flow"] == "xtls-rprx-vision"
    assert config["outbounds"][0]["streamSettings"]["realitySettings"]["publicKey"] == "public-a"
    assert config["routing"]["rules"][1]["ip"] == ["geoip:de"]
    assert config["routing"]["rules"][1]["ruleTag"] == "regional"
    assert not any("domain" in rule and "geosite:category-de" in rule["domain"] for rule in config["routing"]["rules"])
    assert config["routing"]["rules"][-1] == {
        "type": "field",
        "ruleTag": "default",
        "balancerTag": "meridian-pool-gateway-a-primary",
        "network": "tcp,udp",
    }
    assert config["routing"]["balancers"][0]["fallbackTag"] == "block"
    assert config["observatory"]["subjectSelector"] == [
        _EDGE_A_TAG,
        _EDGE_B_TAG,
    ]
    assert config["observatory"]["probeUrl"] == ("https://www.apple.com/library/test/success.html")


def test_gateway_refuses_to_guess_missing_service_credentials() -> None:
    profile = _profile().model_copy(
        update={
            "service_outbounds": [
                ServiceOutboundSpec(
                    edge_id="gateway-a:exit-a",
                    tag="edge-a",
                    service_user_ref="service-user:a",
                    target_workload_ref="exit-a",
                    target_server_ref="srv-exit-a",
                    target_inbound_ref="inbound:bridge",
                    target_port=41001,
                    target_sni="www.microsoft.com",
                )
            ]
        }
    )

    with pytest.raises(WorkloadConfigError, match="credentials do not match"):
        render_workload_config(profile, reality_keys=_keys())


def test_gateway_rejects_prefix_colliding_service_outbound_tags() -> None:
    first = ServiceOutboundSpec(
        edge_id="gateway-a:exit-a",
        tag="meridian-edge-exit-a",
        service_user_ref="service-user:gateway-a:exit-a",
        target_workload_ref="exit-a",
        target_server_ref="srv-exit-a",
        target_inbound_ref="inbound:exit-a:bridge:gateway-a",
        target_port=41001,
        target_sni="www.microsoft.com",
    )
    second = first.model_copy(
        update={
            "edge_id": "gateway-a:exit-a-backup",
            "tag": "meridian-edge-exit-a-backup",
            "service_user_ref": "service-user:gateway-a:exit-a-backup",
            "target_workload_ref": "exit-a-backup",
            "target_server_ref": "srv-exit-a-backup",
            "target_inbound_ref": "inbound:exit-a-backup:bridge:gateway-a",
            "target_port": 41002,
        }
    )
    profile = _profile().model_copy(
        update={
            "service_outbounds": [first, second],
            "egress_balancers": [
                EgressBalancerSpec(
                    pool_id="primary",
                    tag="meridian-pool-gateway-a-primary",
                    outbound_tags=[first.tag, second.tag],
                    strategy="least_ping",
                    probe_url="https://www.apple.com/library/test/success.html",
                )
            ],
        }
    )
    credential = ServiceRouteCredential(
        address="198.51.100.10",
        vless_uuid="11111111-1111-4111-8111-111111111111",
        public_key="public-a",
        short_id="aaaaaaaaaaaaaaaa",
    )

    with pytest.raises(WorkloadConfigError, match="prefix-free"):
        render_workload_config(
            profile,
            reality_keys=_keys(),
            service_credentials={first.edge_id: credential, second.edge_id: credential},
        )


@pytest.mark.parametrize(
    "keys",
    [
        None,
        RealityKeyBinding(public_key="public", private_key="", short_id="short"),
        RealityKeyBinding(public_key="", private_key="private", short_id="short"),
        RealityKeyBinding(public_key="public", private_key="private", short_id=""),
    ],
)
def test_reality_rendering_fails_closed_on_incomplete_keys(
    keys: RealityKeyBinding | None,
) -> None:
    with pytest.raises(WorkloadConfigError, match="incomplete"):
        render_workload_config(_profile(), reality_keys=keys)
