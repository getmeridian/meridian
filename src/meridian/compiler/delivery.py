"""Reviewed Remnawave client templates for honest fallback behavior."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

_PROBE_URL = "https://www.apple.com/library/test/success.html"

ClientRouteAction = Literal["direct", "block"]


@dataclass(frozen=True)
class ClientRoute:
    """One client-side rule, rendered into every delivery format that routes.

    Matchers use Xray vocabulary (``geosite:``, ``geoip:``, ``domain:``); a rule
    matches any listed domain or IP, or exactly the network/port pair.
    """

    action: ClientRouteAction
    domains: tuple[str, ...] = ()
    ips: tuple[str, ...] = ()
    network: Literal["", "udp"] = ""
    port: str = ""


# Russian destinations stay off the tunnel: domestic services refuse foreign
# exits, and under mobile whitelists they are the only thing that opens.
# Foreign QUIC is refused so apps fall back to TCP with a sniffable TLS name.
DEFAULT_CLIENT_ROUTES: tuple[ClientRoute, ...] = (
    ClientRoute("direct", domains=("geosite:private",), ips=("geoip:private",)),
    ClientRoute("direct", domains=("domain:ru", "domain:su", "domain:xn--p1ai", "geosite:category-ru")),
    ClientRoute("direct", ips=("geoip:ru",)),
    ClientRoute("block", network="udp", port="443"),
)

# Route by the sniffed name (IP-only clients and QUIC) without rewriting the destination.
_SNIFFING: dict[str, Any] = {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True}

_XRAY_OUTBOUND: dict[ClientRouteAction, str] = {"direct": "DIRECT", "block": "BLOCK"}
_MIHOMO_TARGET: dict[ClientRouteAction, str] = {"direct": "DIRECT", "block": "REJECT"}


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


def xray_json_template() -> dict[str, Any]:
    """Route through generated Meridian Host outbounds and block all-down."""
    return {
        "remnawave": {
            "injectHosts": [
                {
                    "selector": {
                        "type": "tagRegex",
                        "pattern": "^MERIDIAN_V4_XRAY_EDGE$",
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
                *xray_client_rules(),
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
                "sniffing": _SNIFFING,
            },
            {
                "tag": "HTTP",
                "listen": "127.0.0.1",
                "port": 10809,
                "protocol": "http",
                "sniffing": _SNIFFING,
            },
        ],
        "outbounds": [
            {"tag": "DIRECT", "protocol": "freedom"},
            {"tag": "BLOCK", "protocol": "blackhole"},
        ],
    }


def mihomo_template() -> str:
    """Use all generated Hosts in one ordered health-checked fallback group."""
    rules = "".join(f"  - {rule}\n" for rule in mihomo_client_rules())
    return f"""\
mixed-port: 7890
allow-lan: false
mode: rule
log-level: warning
ipv6: true
proxies: # LEAVE THIS LINE!
proxy-groups:
  - name: MERIDIAN AUTO
    type: fallback
    url: https://www.apple.com/library/test/success.html
    interval: 300
    lazy: true
    proxies: # LEAVE THIS LINE!
rules:
{rules}  - MATCH,MERIDIAN AUTO
"""
