"""Bind compiled policy routes to the connections a particular document can actually use."""

from __future__ import annotations

import copy
from collections.abc import Collection, Mapping
from typing import Any

from meridian.compiler.delivery_dns import mihomo_dns, singbox_dns, xray_dns
from meridian.compiler.delivery_rules import mihomo_client_rules, singbox_client_rules, xray_client_rules
from meridian.control.models import ClientEndpoint
from meridian.core.delivery_capabilities import represent_policy_dns, represent_policy_routes
from meridian.core.delivery_policies import ResolvedDeliveryPolicy


def policy_exit_members(
    policy: ResolvedDeliveryPolicy, endpoints: list[ClientEndpoint]
) -> dict[str, list[ClientEndpoint]]:
    wanted = {rule.action.exit for rule in policy.policy.rules if rule.action.exit is not None}
    return {
        exit_ref: [endpoint for endpoint in endpoints if endpoint.exit_ref == exit_ref]
        for exit_ref in sorted(wanted)
        if any(endpoint.exit_ref == exit_ref for endpoint in endpoints)
    }


def exit_group_names(exits: Collection[str], taken: Collection[str]) -> dict[str, str]:
    used = set(taken)
    result = {}
    for exit_ref in sorted(exits):
        base = f"Meridian exit {exit_ref}"
        name = base
        suffix = 2
        while name in used:
            name = f"{base} ({suffix})"
            suffix += 1
        result[exit_ref] = name
        used.add(name)
    return result


def apply_xray_policy(
    document: dict[str, Any], policy: ResolvedDeliveryPolicy, endpoints: list[ClientEndpoint], tags: Mapping[int, str]
) -> None:
    members = policy_exit_members(policy, endpoints)
    targets = {exit_ref: f"MERIDIAN_EXIT_{exit_ref}" for exit_ref in members}
    routes, _ = represent_policy_routes(policy, "xray-json", available_exits=targets)
    routing = document["routing"]
    automatic = routing["balancers"][0]
    routing["balancers"].extend(
        {**copy.deepcopy(automatic), "tag": targets[exit_ref], "selector": [tags[id(endpoint)] for endpoint in group]}
        for exit_ref, group in members.items()
    )
    routing["rules"] = [
        *xray_client_rules(routes, exit_targets=targets),
        {"type": "field", "network": "tcp,udp", "balancerTag": "MERIDIAN_AUTO"},
    ]
    resolvers, _, _ = represent_policy_dns(policy, "xray-json")
    dns = xray_dns(resolvers)
    if dns is not None:
        document["dns"] = dns
        # Local resolvers retain LAN handling; public DNS sockets use the tunnel.
        routing["rules"].insert(0, {"type": "field", "inboundTag": ["MERIDIAN_DNS"], "balancerTag": "MERIDIAN_AUTO"})
        routing["rules"].insert(
            0, {"type": "field", "inboundTag": ["MERIDIAN_DNS"], "ip": ["geoip:private"], "outboundTag": "DIRECT"}
        )
        routing["domainStrategy"] = "IPOnDemand"
        for outbound in document["outbounds"]:
            if outbound["protocol"] == "freedom":
                outbound["settings"] = {"domainStrategy": "UseIP"}


def apply_mihomo_policy(
    document: dict[str, Any], policy: ResolvedDeliveryPolicy, endpoints: list[ClientEndpoint], *, stash: bool
) -> None:
    members = policy_exit_members(policy, [endpoint for endpoint in endpoints if not endpoint.fingerprint_copy])
    taken = [proxy["name"] for proxy in document["proxies"]] + [group["name"] for group in document["proxy-groups"]]
    targets = exit_group_names(members, taken)
    automatic = next(group for group in document["proxy-groups"] if group["name"] == "Automatic")
    document["proxy-groups"].extend(
        {
            **{key: copy.deepcopy(value) for key, value in automatic.items() if key != "exclude-filter"},
            "name": targets[exit_ref],
            "proxies": [endpoint.remark for endpoint in group],
        }
        for exit_ref, group in members.items()
    )
    routes, _ = represent_policy_routes(policy, "stash" if stash else "mihomo", available_exits=targets)
    document["rules"] = [*mihomo_client_rules(routes, exit_targets=targets), "MATCH,Meridian"]
    resolvers, fake_ip, _ = represent_policy_dns(policy, "stash" if stash else "mihomo")
    dns = mihomo_dns(resolvers, fake_ip=fake_ip, stash=stash)
    if dns is not None:
        document["dns"] = dns
    if not stash:
        # Only the already-installed pinned dat files can supply these lists.
        document.update(
            {
                "geodata-mode": True,
                "geodata-loader": "standard",
                "geo-auto-update": False,
                "geox-url": {"geoip": "", "geosite": "", "mmdb": ""},
            }
        )


def apply_singbox_policy(
    document: dict[str, Any], policy: ResolvedDeliveryPolicy, endpoints: list[ClientEndpoint]
) -> None:
    members = policy_exit_members(policy, endpoints)
    targets = exit_group_names(members, [outbound["tag"] for outbound in document["outbounds"]])
    automatic = next(outbound for outbound in document["outbounds"] if outbound["type"] == "urltest")
    document["outbounds"].extend(
        {**copy.deepcopy(automatic), "tag": targets[exit_ref], "outbounds": [endpoint.remark for endpoint in group]}
        for exit_ref, group in members.items()
    )
    routes, _ = represent_policy_routes(policy, "singbox", available_exits=targets)
    document["route"]["rules"] = [
        {"action": "sniff"},
        {"protocol": "dns", "action": "hijack-dns"},
        *singbox_client_rules(routes, exit_targets=targets),
    ]
    resolvers, fake_ip, _ = represent_policy_dns(policy, "singbox")
    dns = singbox_dns(resolvers, fake_ip=fake_ip)
    if dns is not None:
        document["dns"] = dns
        document["route"]["default_domain_resolver"] = "resolver-1"
        document["experimental"]["cache_file"]["store_fakeip"] = fake_ip
