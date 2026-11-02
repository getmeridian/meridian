"""One ordered representation for preset and custom client routes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from meridian.core.defaults import DEFAULT_CLIENT_ROUTING
from meridian.core.delivery_matchers import DeliveryPortRange, NetworkMatch, PortMatch
from meridian.core.delivery_policies import ClientRoutingPolicy, DeliveryRule, ResolvedDeliveryPolicy

ClientRouteAction = Literal["proxy", "direct", "block", "exit"]


@dataclass(frozen=True)
class ClientRoute:
    """Alternative domain/IP matches, narrowed by network/port; protocol is sniffed."""

    action: ClientRouteAction
    domains: tuple[str, ...] = ()
    ips: tuple[str, ...] = ()
    network: str = ""
    port: str = ""
    protocols: tuple[str, ...] = ()
    ru_split_only: bool = False
    exit_ref: str = ""
    number: int = 0
    origin: Literal["lan", "preset", "rule"] = "preset"
    source_rule: int | None = None


# The preset bytes are a compatibility contract; custom rules join after LAN.
DEFAULT_CLIENT_ROUTES: tuple[ClientRoute, ...] = (
    ClientRoute("direct", domains=("geosite:private",), ips=("geoip:private",), origin="lan"),
    ClientRoute(
        "direct", domains=("domain:ru", "domain:su", "domain:xn--p1ai", "geosite:category-ru"), ru_split_only=True
    ),
    ClientRoute("direct", ips=("geoip:ru",), ru_split_only=True),
    ClientRoute("block", network="udp", port="443"),
    ClientRoute("direct", protocols=("bittorrent",)),
)


def client_routes(policy: ClientRoutingPolicy = DEFAULT_CLIENT_ROUTING) -> tuple[ClientRoute, ...]:
    return tuple(route for route in DEFAULT_CLIENT_ROUTES if not route.ru_split_only or policy == "ru-split")


def policy_routes(resolved: ResolvedDeliveryPolicy) -> tuple[ClientRoute, ...]:
    preset = client_routes(resolved.policy.preset)
    rules = [
        preset[0],
        *(compile_rule(rule, index) for index, rule in enumerate(resolved.policy.rules, 1)),
        *preset[1:],
    ]
    return tuple(replace(rule, number=index) for index, rule in enumerate(rules, 1))


def compile_rule(rule: DeliveryRule, number: int) -> ClientRoute:
    domains: tuple[str, ...] = ()
    ips: tuple[str, ...] = ()
    network: str = rule.network or ""
    port = port_text(rule.ports)
    match = rule.match
    if isinstance(match, PortMatch):
        port = port_text(match.values)
    elif isinstance(match, NetworkMatch):
        network = ",".join(match.values)
    elif match.kind in {"geoip", "cidr"}:
        ips = tuple(f"geoip:{value}" if match.kind == "geoip" else value for value in match.values)
    else:
        prefix = {"domain_suffix": "domain", "domain_full": "full", "domain_keyword": "keyword", "geosite": "geosite"}[
            match.kind
        ]
        domains = tuple(f"{prefix}:{value}" for value in match.values)
    return ClientRoute(
        action=rule.action.kind,
        domains=domains,
        ips=ips,
        network=network,
        port=port,
        exit_ref=rule.action.exit or "",
        origin="rule",
        source_rule=number,
    )


def port_text(ports: list[DeliveryPortRange]) -> str:
    return ",".join(port.text() for port in ports)
