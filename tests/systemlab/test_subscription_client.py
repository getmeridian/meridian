from __future__ import annotations

import json

import pytest

from tests.systemlab.subscription_client import (
    endpoint_addresses,
    missing_addresses,
    parse_xray_subscription,
    replace_user_ids,
    route_only,
    select_outbound,
    use_free_local_ports,
)


def _canonical_config() -> dict:
    return {
        "inbounds": [
            {
                "tag": "SOCKS",
                "listen": "127.0.0.1",
                "port": 10808,
                "protocol": "socks",
                "settings": {"udp": True},
            },
            {
                "tag": "HTTP",
                "listen": "127.0.0.1",
                "port": 10809,
                "protocol": "http",
            },
        ],
        "outbounds": [
            {
                "tag": "MERIDIAN_PROXY_1",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": "198.51.100.10",
                            "port": 443,
                            "users": [{"id": ("11111111-1111-1111-1111-111111111111")}],
                        }
                    ]
                },
            },
            {
                "tag": "MERIDIAN_PROXY_2",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": "198.51.100.20",
                            "port": 443,
                            "users": [{"id": ("11111111-1111-1111-1111-111111111111")}],
                        }
                    ]
                },
            },
            {"tag": "BLOCK", "protocol": "blackhole"},
        ],
        "routing": {
            "rules": [
                {
                    "type": "field",
                    "network": "tcp,udp",
                    "balancerTag": "MERIDIAN_AUTO",
                }
            ],
            "balancers": [
                {
                    "tag": "MERIDIAN_AUTO",
                    "selector": ["MERIDIAN_PROXY"],
                }
            ],
        },
        "burstObservatory": {"subjectSelector": ["MERIDIAN_PROXY"]},
    }


def test_parses_only_rendered_canonical_xray_documents() -> None:
    parsed = parse_xray_subscription(json.dumps(_canonical_config()))

    assert endpoint_addresses(parsed) == {
        "198.51.100.10",
        "198.51.100.20",
    }
    assert missing_addresses(
        parsed,
        ["198.51.100.10", "198.51.100.30"],
    ) == {"198.51.100.30"}

    with pytest.raises(ValueError, match="not JSON"):
        parse_xray_subscription("dmxlc3M6Ly8=")
    with pytest.raises(ValueError, match="no Meridian"):
        parse_xray_subscription('{"outbounds": [{"tag": "DIRECT"}]}')


def test_selects_delivered_path_without_rebuilding_credentials() -> None:
    canonical = _canonical_config()
    selected = route_only(
        canonical,
        select_outbound(canonical, "198.51.100.20"),
    )

    assert selected["routing"]["rules"][-1]["outboundTag"] == ("MERIDIAN_PROXY_2")
    assert "balancers" not in selected["routing"]
    assert "burstObservatory" not in selected
    assert canonical["routing"]["balancers"]


def test_changes_only_test_credentials_and_local_listener_ports() -> None:
    canonical = _canonical_config()
    corrupted = replace_user_ids(
        canonical,
        "00000000-0000-0000-0000-000000000000",
    )
    runnable, socks_port = use_free_local_ports(corrupted)

    assert socks_port != 10808
    assert runnable["inbounds"][0]["port"] == socks_port
    assert {
        user["id"]
        for outbound in runnable["outbounds"][:2]
        for destination in outbound["settings"]["vnext"]
        for user in destination["users"]
    } == {"00000000-0000-0000-0000-000000000000"}
    assert canonical["outbounds"][0]["settings"]["vnext"][0]["users"][0]["id"].startswith("1111")
