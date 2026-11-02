"""Reviewed Remnawave client templates for honest fallback behavior."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from meridian.compiler.delivery_rules import mihomo_client_rules, singbox_client_rules, xray_client_rules
from meridian.compiler.errors import TopologyCompileError
from meridian.core.defaults import DEFAULT_CLIENT_ROUTING
from meridian.core.delivery_policy_ir import DEFAULT_CLIENT_ROUTES as DEFAULT_CLIENT_ROUTES
from meridian.core.delivery_policy_ir import ClientRoute as ClientRoute
from meridian.core.delivery_policy_ir import ClientRouteAction as ClientRouteAction
from meridian.core.delivery_policy_ir import client_routes
from meridian.core.topology import ClientRoutingPolicy, SetupIntent

_PROBE_URL = "https://www.apple.com/library/test/success.html"

_REGEX_SPECIAL = frozenset("\\.^$|?*+()[]{}")


def validate_delivery_backend(intent: SetupIntent) -> None:
    if intent.control.backend != "meridian" and (
        intent.delivery.delivery_policies
        or intent.delivery.default_policy is not None
        or intent.delivery.profile_policies
    ):
        raise TopologyCompileError(
            "Named delivery policies need Meridian's control service. Run meridian migrate first."
        )
    if intent.control.backend != "meridian" and intent.delivery.response_rules:
        raise TopologyCompileError("App response rules need Meridian's control service. Run meridian migrate first.")


# Route by the sniffed name (IP-only clients and QUIC) without rewriting the destination.
# enabled also sniffs BitTorrent; destOverride controls domain overrides, not
# protocol detection. Xray rejects "bittorrent" as a destOverride value.
ROUTE_ONLY_SNIFFING: dict[str, Any] = {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True}


def xray_json_template(
    edge_tag: str = "MERIDIAN_V4_XRAY_EDGE", *, client_routing: ClientRoutingPolicy = DEFAULT_CLIENT_ROUTING
) -> dict[str, Any]:
    """Route through the hidden edge Hosts tagged *edge_tag* and block all-down."""
    return {
        "remnawave": {
            "injectHosts": [
                {
                    "selector": {
                        "type": "tagRegex",
                        "pattern": f"^{edge_tag}$",
                    },
                    "selectFrom": "HIDDEN",
                    "tagPrefix": "MERIDIAN_PROXY",
                }
            ]
        },
        "log": {"loglevel": "warning"},
        "dns": {"servers": ["1.1.1.1", "8.8.8.8"]},
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                *xray_client_rules(client_routes(client_routing)),
                {
                    "type": "field",
                    "network": "tcp,udp",
                    "balancerTag": "MERIDIAN_AUTO",
                },
            ],
            "balancers": [
                {
                    "tag": "MERIDIAN_AUTO",
                    "selector": ["MERIDIAN_PROXY"],
                    "strategy": {
                        "type": "leastLoad",
                        "settings": {
                            "maxRTT": "1s",
                            "expected": 1,
                            "baselines": ["1s"],
                            "tolerance": 0.01,
                        },
                    },
                    "fallbackTag": "BLOCK",
                }
            ],
        },
        "burstObservatory": {
            "subjectSelector": ["MERIDIAN_PROXY"],
            "pingConfig": {
                "destination": _PROBE_URL,
                "connectivity": "",
                "interval": "1m",
                "sampling": 1,
                "timeout": "3s",
            },
        },
        "inbounds": [
            {
                "tag": "SOCKS",
                "listen": "127.0.0.1",
                "port": 10808,
                "protocol": "socks",
                "settings": {"udp": True},
                "sniffing": ROUTE_ONLY_SNIFFING,
            },
            {
                "tag": "HTTP",
                "listen": "127.0.0.1",
                "port": 10809,
                "protocol": "http",
                "sniffing": ROUTE_ONLY_SNIFFING,
            },
        ],
        "outbounds": [
            {"tag": "DIRECT", "protocol": "freedom"},
            {"tag": "BLOCK", "protocol": "blackhole"},
        ],
    }


def mihomo_template(
    exclude_from_failover: Sequence[str] = (), *, client_routing: ClientRoutingPolicy = DEFAULT_CLIENT_ROUTING
) -> str:
    """Pick entries by hand or let an ordered, health-checked fallback group choose.

    Remnawave appends every entry to both groups; *exclude_from_failover* names entries the
    fallback group must skip (fingerprint copies), matched exactly.
    """
    rules = "".join(f"  - {rule}\n" for rule in mihomo_client_rules(client_routes(client_routing)))
    exclude = ""
    if exclude_from_failover:
        pattern = "|".join(_regex_literal(name) for name in exclude_from_failover)
        exclude = f"    exclude-filter: {json.dumps(f'^({pattern})$', ensure_ascii=False)}\n"
    return f"""\
mixed-port: 7890
allow-lan: false
mode: rule
log-level: warning
ipv6: true
proxies: # LEAVE THIS LINE!
proxy-groups:
  - name: Meridian
    type: select
    proxies: # LEAVE THIS LINE!
      - Automatic
  - name: Automatic
    type: fallback
    url: {_PROBE_URL}
    interval: 300
    lazy: true
{exclude}    proxies: # LEAVE THIS LINE!
rules:
{rules}  - MATCH,Meridian
"""


def _regex_literal(value: str) -> str:
    return "".join(f"\\{char}" if char in _REGEX_SPECIAL else char for char in value)


def singbox_template(*, client_routing: ClientRoutingPolicy = DEFAULT_CLIENT_ROUTING) -> dict[str, Any]:
    """Pick the lowest-latency working Host automatically, with every Host also selectable.

    Remnawave fills each selector and urltest with the user's proxies; it skips XHTTP,
    which sing-box does not speak. Nothing is downloaded at startup.
    """
    return {
        "log": {"level": "warn", "timestamp": True},
        "dns": {
            "servers": [
                {"tag": "cf-dns", "type": "tls", "server": "1.1.1.1"},
                {"tag": "local", "type": "udp", "server": "1.1.1.1"},
                {"tag": "remote", "type": "fakeip", "inet4_range": "198.18.0.0/15", "inet6_range": "fc00::/18"},
            ],
            "rules": [{"query_type": ["A", "AAAA"], "server": "remote"}],
        },
        "inbounds": [
            {
                "tag": "tun-in",
                "type": "tun",
                "address": ["172.19.0.1/30", "fdfe:dcba:9876::1/126"],
                "mtu": 9000,
                "auto_route": True,
                "strict_route": True,
                "stack": "mixed",
                "platform": {"http_proxy": {"enabled": True, "server": "127.0.0.1", "server_port": 2412}},
            },
            {"tag": "mixed-in", "type": "mixed", "listen": "127.0.0.1", "listen_port": 2412},
        ],
        "outbounds": [
            {
                "tag": "Meridian",
                "type": "selector",
                "outbounds": None,
                "default": "Automatic",
                "interrupt_exist_connections": True,
            },
            {
                "tag": "Automatic",
                "type": "urltest",
                "outbounds": None,
                "url": _PROBE_URL,
                "interval": "3m",
                "tolerance": 50,
                "interrupt_exist_connections": True,
            },
            {"tag": "direct", "type": "direct"},
        ],
        "route": {
            "rules": [
                {"action": "sniff"},
                {"protocol": "dns", "action": "hijack-dns"},
                *singbox_client_rules(client_routes(client_routing)),
            ],
            "final": "Meridian",
            "auto_detect_interface": True,
            "default_domain_resolver": "local",
        },
        "experimental": {
            "cache_file": {"enabled": True, "store_fakeip": True},
            "clash_api": {"external_controller": "127.0.0.1:9090", "default_mode": "rule"},
        },
    }
