"""V4 workload rendering keeps protocol and secret state isolated."""

from __future__ import annotations

import pytest

from meridian.cluster import RealityKeyBinding
from meridian.compiler.models import ConfigProfilePayload, InboundPayload
from meridian.xray_workload import WorkloadConfigError, render_workload_config


def _profile(*, warp: bool = False) -> ConfigProfilePayload:
    inbounds = [
        InboundPayload(
            workload_ref="exit-a",
            protocol="reality",
            tag="meridian-exit-a-reality",
            listen_port=10443,
            public_port=443,
            reality_sni="www.microsoft.com",
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
        name="Meridian v4 / exit-a",
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
        "serverNames": ["www.microsoft.com"],
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
