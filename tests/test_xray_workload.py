"""V4 workload rendering keeps protocol and secret state isolated."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

import pytest

from meridian.cluster import RealityKeyBinding
from meridian.compiler import compile_topology
from meridian.compiler.models import (
    ConfigProfilePayload,
    EgressBalancerSpec,
    InboundPayload,
    ServiceOutboundSpec,
    WorkloadRouteSpec,
    certificate_directory,
)
from meridian.core.topology import OrderedRouteIntent, RoutingGatewayIntent
from meridian.xray_workload import (
    EXIT_DOH_RESOLVERS,
    ServiceRouteCredential,
    WorkloadConfigError,
    render_workload_config,
)
from tests.support.topology import single_exit_intent

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
        "minClientVer": "1.8.0",
    }
    # Left unset, Xray 26.7.11+ requires client 26.3.27 and rejects Mihomo (1.8.2) and sing-box.
    floor = reality["streamSettings"]["realitySettings"]["minClientVer"]
    assert tuple(map(int, floor.split("."))) <= (1, 8, 2)
    assert config["inbounds"][1]["streamSettings"]["xhttpSettings"]["path"] == "/xhttp-a"
    assert config["inbounds"][2]["streamSettings"]["wsSettings"]["path"] == "/ws-a"
    hysteria_tls = config["inbounds"][3]["streamSettings"]["tlsSettings"]
    assert hysteria_tls["alpn"] == ["h3"]
    # Must match where the certificate resource issues the Hysteria2 hostname.
    cert_dir = certificate_directory("vpn-a.example.com")
    assert hysteria_tls["certificates"] == [
        {"certificateFile": f"{cert_dir}/fullchain.pem", "keyFile": f"{cert_dir}/key.pem"}
    ]
    assert [outbound["tag"] for outbound in config["outbounds"]] == ["direct", "block"]


def test_hysteria2_hides_quic_behind_a_stable_salamander_password() -> None:
    from meridian.xray_workload import salamander_password

    config = render_workload_config(_profile(), reality_keys=_keys())
    mask = config["inbounds"][3]["streamSettings"]["finalmask"]

    assert mask == {"udp": [{"type": "salamander", "settings": {"password": salamander_password(_keys())}}]}
    password = mask["udp"][0]["settings"]["password"]
    # Same keys, same password on every render: observation must never see drift.
    assert render_workload_config(_profile(), reality_keys=_keys()) == config
    assert len(password) == 32 and "private-a" not in password
    rotated = RealityKeyBinding(public_key="public-b", private_key="private-b", short_id="0123456789abcdef")
    assert salamander_password(rotated) != password


def test_geo_block_refuses_russian_destinations_only_when_reviewed() -> None:
    plain = render_workload_config(_profile(), reality_keys=_keys())
    blocked = render_workload_config(_profile().model_copy(update={"geo_block": True}), reality_keys=_keys())

    geo_rules = [
        {"type": "field", "domain": ["geosite:category-ru"], "outboundTag": "block"},
        {"type": "field", "ip": ["geoip:ru"], "outboundTag": "block"},
    ]
    assert not any(rule in plain["routing"]["rules"] for rule in geo_rules)
    assert all(rule in blocked["routing"]["rules"] for rule in geo_rules)


def test_every_inbound_sniffs_route_only_so_torrent_blocker_can_see_bittorrent() -> None:
    config = render_workload_config(_profile(), reality_keys=_keys())

    assert config["inbounds"]
    for inbound in config["inbounds"]:
        assert inbound["sniffing"] == {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True}


def test_access_logging_is_disabled() -> None:
    config = render_workload_config(_profile(), reality_keys=_keys())

    assert config["log"] == {"loglevel": "warning", "access": "none"}


@pytest.mark.parametrize("warp", [False, True], ids=["direct", "warp"])
def test_every_lookup_goes_to_ip_addressed_doh_and_never_the_host_resolver(warp: bool) -> None:
    dns = render_workload_config(_profile(warp=warp), reality_keys=_keys())["dns"]

    hosts = []
    for server in dns["servers"]:
        url = urlsplit(server["address"])
        # "+local" dials from the host, so a lookup never rides the routes it resolves for;
        # a hostname would need a plain bootstrap lookup, and "localhost" is the host resolver.
        assert url.scheme == "https+local", server
        hosts.append(ipaddress.ip_address(url.hostname or ""))
    # Fallbacks exist on both families, so a single-stack exit always has one to ask.
    assert {host.version for host in hosts} == {4, 6}
    assert len({resolver.operator for resolver in EXIT_DOH_RESOLVERS}) >= 2
    # Only the families this host can route: an IPv4-only exit never waits on AAAA answers.
    assert dns["queryStrategy"] == "UseSystem"
    assert dns.get("disableFallback") is not True


@pytest.mark.parametrize("warp", [False, True], ids=["direct", "warp"])
def test_direct_egress_resolves_through_doh_and_refuses_unresolved_names(warp: bool) -> None:
    outbounds = render_workload_config(_profile(warp=warp), reality_keys=_keys())["outbounds"]
    direct = next(outbound for outbound in outbounds if outbound["tag"] == "direct")

    # AsIs and every UseIP* strategy hand a name the DNS module could not answer to the
    # host's plain resolver; ForceIP* fails the connection instead. IPv4 first keeps an
    # IPv4-only exit from dialing an AAAA answer.
    assert direct["settings"] == {"domainStrategy": "ForceIPv4v6"}


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


def test_gateway_direct_rule_leaves_through_the_gateways_freedom_outbound() -> None:
    intent = single_exit_intent(
        routing_gateways=[RoutingGatewayIntent(id="gateway-a", server_ref="srv-gateway", bridge_path_ref="reality-a")],
        routes=[
            OrderedRouteIntent(id="domestic", priority=10, match="country", match_values=["RU"], action="direct"),
            OrderedRouteIntent(id="ads", priority=20, match="domain", match_values=["ads.example.com"], action="block"),
        ],
    )
    profile = next(
        resource.payload
        for resource in compile_topology(intent).resources
        if resource.logical_id == "profile:gateway-a"
    )
    assert isinstance(profile, ConfigProfilePayload)
    (edge,) = profile.service_outbounds
    credential = ServiceRouteCredential(
        address="198.51.100.20",
        vless_uuid="11111111-1111-4111-8111-111111111111",
        public_key="public-a",
        short_id="aaaaaaaaaaaaaaaa",
    )

    config = render_workload_config(profile, reality_keys=_keys(), service_credentials={edge.edge_id: credential})

    outbounds = {outbound["tag"]: outbound for outbound in config["outbounds"]}
    assert outbounds["direct"] == {"tag": "direct", "protocol": "freedom"}
    rules = [(rule.get("ruleTag"), rule.get("outboundTag")) for rule in config["routing"]["rules"]]
    assert rules == [
        (None, "block"),  # private ranges first, always
        ("domestic", "direct"),
        ("ads", "block"),
        ("default", edge.tag),
    ]
    assert config["routing"]["rules"][1]["ip"] == ["geoip:ru"]


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
