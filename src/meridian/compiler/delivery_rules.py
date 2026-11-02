"""Compile the one client route representation into each client's finite syntax."""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping
from typing import Any

from meridian.core.delivery_policy_ir import DEFAULT_CLIENT_ROUTES, ClientRoute


def xray_client_rules(
    routes: tuple[ClientRoute, ...] = DEFAULT_CLIENT_ROUTES, *, exit_targets: Mapping[str, str] | None = None
) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for route in routes:
        action: dict[str, Any]
        if route.action == "proxy":
            action = {"balancerTag": "MERIDIAN_AUTO"}
        elif route.action == "exit":
            target = (exit_targets or {}).get(route.exit_ref)
            action = {"balancerTag": target} if target is not None else {"outboundTag": "BLOCK"}
        else:
            action = {"outboundTag": "DIRECT" if route.action == "direct" else "BLOCK"}
        conditions: dict[str, Any] = {}
        if route.network:
            conditions["network"] = route.network
        if route.port:
            conditions["port"] = route.port
        matches = [
            (key, list(values))
            for key, values in (("domain", route.domains), ("ip", route.ips), ("protocol", route.protocols))
            if values
        ]
        if matches:
            rules.extend({"type": "field", key: values, **conditions, **action} for key, values in matches)
        elif conditions:
            rules.append({"type": "field", **conditions, **action})
    return rules


def mihomo_client_rules(
    routes: tuple[ClientRoute, ...] = DEFAULT_CLIENT_ROUTES, *, exit_targets: Mapping[str, str] | None = None
) -> list[str]:
    rules = []
    for route in routes:
        target = {"proxy": "Meridian", "direct": "DIRECT", "block": "REJECT"}.get(route.action)
        if target is None:
            target = (exit_targets or {}).get(route.exit_ref, "REJECT")
        qualifiers = []
        if route.network:
            qualifiers.append(_alternatives([f"NETWORK,{network.upper()}" for network in route.network.split(",")]))
        if route.port:
            qualifiers.append(_alternatives([f"DST-PORT,{port}" for port in route.port.split(",")]))
        for domain in route.domains:
            rules.append(_qualified_rule(_domain_condition(domain), qualifiers, target))
        for address in route.ips:
            condition = _ip_condition(address)
            # no-resolve belongs inside logical subrules, after the target on a simple rule.
            rules.append(
                _qualified_rule(condition + ",no-resolve" if qualifiers else condition, qualifiers, target)
                + (",no-resolve" if not qualifiers else "")
            )
        if not (route.domains or route.ips or route.protocols) and qualifiers:
            rules.append(_qualified_rule("", qualifiers, target))
    return rules


def _domain_condition(matcher: str) -> str:
    kind, _, value = matcher.partition(":")
    return (
        {"domain": "DOMAIN-SUFFIX", "keyword": "DOMAIN-KEYWORD", "full": "DOMAIN", "geosite": "GEOSITE"}[kind]
        + ","
        + value
    )


def _ip_condition(matcher: str) -> str:
    if matcher.startswith("geoip:"):
        value = matcher.removeprefix("geoip:")
        return f"GEOIP,{value.upper() if value != 'private' else value}"
    kind = "IP-CIDR6" if ipaddress.ip_network(matcher).version == 6 else "IP-CIDR"
    return f"{kind},{matcher}"


def _alternatives(conditions: list[str]) -> str:
    return (
        conditions[0] if len(conditions) == 1 else "OR,(" + ",".join(f"({condition})" for condition in conditions) + ")"
    )


def _qualified_rule(condition: str, qualifiers: list[str], target: str) -> str:
    conditions = [*([condition] if condition else []), *qualifiers]
    if len(conditions) == 1:
        return f"{conditions[0]},{target}"
    return "AND,(" + ",".join(f"({part})" for part in conditions) + f"),{target}"


def singbox_client_rules(
    routes: tuple[ClientRoute, ...] = DEFAULT_CLIENT_ROUTES, *, exit_targets: Mapping[str, str] | None = None
) -> list[dict[str, Any]]:
    rules = []
    for route in routes:
        if route.action == "block" or (route.action == "exit" and route.exit_ref not in (exit_targets or {})):
            action: dict[str, Any] = {"action": "reject"}
        else:
            target = "direct" if route.action == "direct" else (exit_targets or {}).get(route.exit_ref, "Meridian")
            action = {"outbound": target}
        conditions: dict[str, Any] = {}
        if route.network:
            conditions["network"] = route.network.split(",")
        if route.port:
            ports = route.port.split(",")
            exact = [int(port) for port in ports if "-" not in port]
            ranges = [port.replace("-", ":") for port in ports if "-" in port]
            if exact:
                conditions["port"] = exact
            if ranges:
                conditions["port_range"] = ranges
        domain_fields = {"domain": "domain_suffix", "keyword": "domain_keyword", "full": "domain"}
        for kind, field in domain_fields.items():
            values = [
                value for prefix, _, value in (matcher.partition(":") for matcher in route.domains) if prefix == kind
            ]
            if kind == "domain" and route.origin != "rule":
                values = ["." + value for value in values]
            if values:
                rules.append({field: values, **conditions, **action})
        if "geoip:private" in route.ips:
            rules.append({"ip_is_private": True, **conditions, **action})
        cidrs = [value for value in route.ips if not value.startswith("geoip:")]
        if cidrs:
            rules.append({"ip_cidr": cidrs, **conditions, **action})
        if not (route.domains or route.ips or route.protocols) and conditions:
            rules.append({**conditions, **action})
    return rules
