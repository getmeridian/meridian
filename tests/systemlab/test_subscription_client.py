from __future__ import annotations

import base64
import json
from unittest.mock import patch

import pytest
import yaml

from tests.systemlab.subscription_client import (
    base64_endpoint_addresses,
    base64_vless_user_ids,
    endpoint_addresses,
    mihomo_endpoint_addresses,
    mihomo_vless_user_ids,
    missing_addresses,
    parse_base64_subscription,
    parse_mihomo_subscription,
    parse_xray_subscription,
    replace_user_ids,
    route_only,
    select_outbound,
    use_free_local_ports,
    xray_vless_user_ids,
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


def _mihomo_config() -> dict:
    return {
        "mixed-port": 7890,
        "proxies": [
            {
                "name": "exit-a",
                "type": "vless",
                "server": "198.51.100.10",
                "port": 443,
                "uuid": "11111111-1111-1111-1111-111111111111",
            },
            {
                "name": "exit-b",
                "type": "vless",
                "server": "198.51.100.20",
                "port": 443,
                "uuid": "11111111-1111-1111-1111-111111111111",
            },
        ],
        "proxy-groups": [
            {
                "name": "MERIDIAN AUTO",
                "type": "fallback",
                "proxies": ["exit-a", "exit-b"],
            }
        ],
        "rules": ["MATCH,MERIDIAN AUTO"],
    }


def test_validates_base64_proxy_urls_and_credentials() -> None:
    encoded = base64.b64encode(
        b"\n".join(
            [
                b"vless://11111111-1111-1111-1111-111111111111@198.51.100.10:443?type=tcp",
                b"vless://11111111-1111-1111-1111-111111111111@198.51.100.20:443?type=tcp",
            ]
        )
    ).decode("ascii")

    urls = parse_base64_subscription(encoded.rstrip("="))

    assert base64_endpoint_addresses(urls) == {"198.51.100.10", "198.51.100.20"}
    assert base64_vless_user_ids(urls) == {"11111111-1111-1111-1111-111111111111"}
    with pytest.raises(ValueError, match="not valid Base64"):
        parse_base64_subscription("nonempty but invalid")
    malformed = base64.b64encode(b"vless://not-a-uuid@198.51.100.10:443").decode("ascii")
    with pytest.raises(ValueError, match="invalid VLESS UUID"):
        parse_base64_subscription(malformed)


def test_validates_mihomo_fallback_graph_and_proxy_endpoints() -> None:
    config = parse_mihomo_subscription(yaml.safe_dump(_mihomo_config()))

    assert mihomo_endpoint_addresses(config) == {"198.51.100.10", "198.51.100.20"}
    assert mihomo_vless_user_ids(config) == {"11111111-1111-1111-1111-111111111111"}
    with pytest.raises(ValueError, match="no proxies"):
        parse_mihomo_subscription("rules:\n  - MATCH,MERIDIAN AUTO\n")

    unknown_member = _mihomo_config()
    unknown_member["proxy-groups"][0]["proxies"].append("missing")
    with pytest.raises(ValueError, match="unknown proxy"):
        parse_mihomo_subscription(yaml.safe_dump(unknown_member))


def test_parses_only_rendered_canonical_xray_documents() -> None:
    parsed = parse_xray_subscription(json.dumps([_canonical_config()]))

    assert endpoint_addresses(parsed) == {
        "198.51.100.10",
        "198.51.100.20",
    }
    assert xray_vless_user_ids(parsed) == {"11111111-1111-1111-1111-111111111111"}
    assert missing_addresses(
        parsed,
        ["198.51.100.10", "198.51.100.30"],
    ) == {"198.51.100.30"}

    with pytest.raises(ValueError, match="not JSON"):
        parse_xray_subscription("dmxlc3M6Ly8=")
    with pytest.raises(ValueError, match="no Meridian"):
        parse_xray_subscription('{"outbounds": [{"tag": "DIRECT"}]}')
    with pytest.raises(ValueError, match="exactly one config"):
        parse_xray_subscription(json.dumps([_canonical_config(), _canonical_config()]))

    assert parse_xray_subscription(json.dumps(_canonical_config()))["routing"]


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


def test_assigns_distinct_local_ports_to_every_inbound() -> None:
    with patch(
        "tests.systemlab.subscription_client._free_port",
        side_effect=[12000, 12000, 12001],
    ):
        runnable, socks_port = use_free_local_ports(_canonical_config())

    assert socks_port == 12000
    assert [inbound["port"] for inbound in runnable["inbounds"]] == [12000, 12001]
