"""Reviewed Remnawave client templates for honest fallback behavior."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from meridian.core.defaults import DEFAULT_CLIENT_ROUTING
from meridian.core.topology import ClientRoutingPolicy

_PROBE_URL = "https://www.apple.com/library/test/success.html"

_REGEX_SPECIAL = frozenset("\\.^$|?*+()[]{}")

ClientRouteAction = Literal["direct", "block"]


@dataclass(frozen=True)
class ClientRoute:
    """One client-side rule, rendered into every delivery format that routes.

    Matchers use Xray vocabulary (``geosite:``, ``geoip:``, ``domain:``); a rule
    matches any listed domain or IP, exactly the network/port pair, or a sniffed
    protocol. Protocol matchers are currently rendered only for Xray JSON;
    Mihomo and sing-box keep their existing routing behavior.
    """

    action: ClientRouteAction
    domains: tuple[str, ...] = ()
    ips: tuple[str, ...] = ()
    network: Literal["", "udp"] = ""
    port: str = ""
    protocols: tuple[str, ...] = ()
    ru_split_only: bool = False


# Russian destinations stay off the tunnel: domestic services refuse foreign
# exits, and under mobile whitelists they are the only thing that opens.
# Foreign QUIC is refused so apps fall back to TCP with a sniffable TLS name.
# Recognized BitTorrent peer handshakes leave from the client's own connection.
# This is best-effort: encrypted peers and UDP DHT/uTP may still use the tunnel.
DEFAULT_CLIENT_ROUTES: tuple[ClientRoute, ...] = (
    ClientRoute("direct", domains=("geosite:private",), ips=("geoip:private",)),
    ClientRoute(
        "direct", domains=("domain:ru", "domain:su", "domain:xn--p1ai", "geosite:category-ru"), ru_split_only=True
    ),
    ClientRoute("direct", ips=("geoip:ru",), ru_split_only=True),
    ClientRoute("block", network="udp", port="443"),
    ClientRoute("direct", protocols=("bittorrent",)),
)

# Route by the sniffed name (IP-only clients and QUIC) without rewriting the destination.
# enabled also sniffs BitTorrent; destOverride controls domain overrides, not
# protocol detection. Xray rejects "bittorrent" as a destOverride value.
ROUTE_ONLY_SNIFFING: dict[str, Any] = {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True}

_XRAY_OUTBOUND: dict[ClientRouteAction, str] = {"direct": "DIRECT", "block": "BLOCK"}
_MIHOMO_TARGET: dict[ClientRouteAction, str] = {"direct": "DIRECT", "block": "REJECT"}


def client_routes(policy: ClientRoutingPolicy = DEFAULT_CLIENT_ROUTING) -> tuple[ClientRoute, ...]:
    """Derive both reviewed policies from the one ordered client list.

    Full routing removes only the Russian bypasses. Private/LAN bypasses,
    QUIC blocking, and the best-effort BitTorrent bypass stay in both policies.
    """
    return tuple(route for route in DEFAULT_CLIENT_ROUTES if not route.ru_split_only or policy == "ru-split")


def xray_client_rules(routes: tuple[ClientRoute, ...] = DEFAULT_CLIENT_ROUTES) -> list[dict[str, Any]]:
    """Render client routes as Xray field rules (domains and IPs as separate rules)."""
    rules: list[dict[str, Any]] = []
    for route in routes:
        outbound = _XRAY_OUTBOUND[route.action]
        if route.domains:
            rules.append({"type": "field", "domain": list(route.domains), "outboundTag": outbound})
        if route.ips:
            rules.append({"type": "field", "ip": list(route.ips), "outboundTag": outbound})
        if route.network:
            rules.append({"type": "field", "network": route.network, "port": route.port, "outboundTag": outbound})
        if route.protocols:
            rules.append({"type": "field", "protocol": list(route.protocols), "outboundTag": outbound})
    return rules


def mihomo_client_rules(routes: tuple[ClientRoute, ...] = DEFAULT_CLIENT_ROUTES) -> list[str]:
    """Render client routes as Mihomo rule lines."""
    rules: list[str] = []
    for route in routes:
        target = _MIHOMO_TARGET[route.action]
        rules.extend(f"{_mihomo_domain(domain)},{target}" for domain in route.domains)
        # no-resolve keeps names off the local resolver: spoofed answers point at domestic stub IPs.
        rules.extend(f"{_mihomo_ip(ip)},{target},no-resolve" for ip in route.ips)
        if route.network:
            rules.append(f"AND,((NETWORK,{route.network.upper()}),(DST-PORT,{route.port})),{target}")
    return rules


def singbox_client_rules(routes: tuple[ClientRoute, ...] = DEFAULT_CLIENT_ROUTES) -> list[dict[str, Any]]:
    """Render client routes as sing-box rules, inline matchers only.

    sing-box refuses to start when a remote rule set cannot be fetched, which a
    mobile whitelist guarantees, so geosite/geoip lists other than private are skipped.
    """
    rules: list[dict[str, Any]] = []
    for route in routes:
        action: dict[str, Any] = {"outbound": "direct"} if route.action == "direct" else {"action": "reject"}
        suffixes = [f".{value}" for kind, _, value in (m.partition(":") for m in route.domains) if kind == "domain"]
        if suffixes:
            rules.append({"domain_suffix": suffixes, **action})
        if "geoip:private" in route.ips:
            rules.append({"ip_is_private": True, **action})
        if route.network:
            rules.append({"network": [route.network], "port": [int(route.port)], **action})
    return rules


def _mihomo_domain(matcher: str) -> str:
    kind, _, value = matcher.partition(":")
    if kind == "geosite":
        return f"GEOSITE,{value}"
    if kind == "domain":
        return f"DOMAIN-SUFFIX,{value}"
    raise ValueError(f"Unsupported client domain matcher: {matcher}")


def _mihomo_ip(matcher: str) -> str:
    kind, _, value = matcher.partition(":")
    if kind == "geoip":
        return f"GEOIP,{value.upper() if value != 'private' else value}"
    raise ValueError(f"Unsupported client IP matcher: {matcher}")


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
