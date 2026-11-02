"""Finite DNS fragments; resolvers have IP addresses, so no hostname bootstrap is needed."""

from __future__ import annotations

from typing import Any

from meridian.core.delivery_matchers import DnsResolver, DohResolver, DotResolver, PlainResolver, resolver_uri


def xray_dns(resolvers: list[DnsResolver]) -> dict[str, Any] | None:
    if not resolvers:
        return None
    servers: list[Any] = []
    for resolver in resolvers:
        if isinstance(resolver, PlainResolver) and resolver.protocol == "udp" and resolver.port != 53:
            servers.append({"address": resolver.address, "port": resolver.port})
        else:
            servers.append(resolver_uri(resolver))
    return {"servers": servers, "tag": "MERIDIAN_DNS", "queryStrategy": "UseIP"}


def mihomo_dns(resolvers: list[DnsResolver], *, fake_ip: bool, stash: bool) -> dict[str, Any] | None:
    if not resolvers:
        return None
    servers = [resolver_uri(resolver) for resolver in resolvers]
    dns: dict[str, Any] = {"enable": True, "ipv6": True, "nameserver": servers, "default-nameserver": servers}
    if not stash:
        dns.update(
            {
                "enhanced-mode": "fake-ip" if fake_ip else "redir-host",
                "respect-rules": True,
                "proxy-server-nameserver": servers,
            }
        )
        if fake_ip:
            dns["fake-ip-range"] = "198.18.0.1/16"
    return dns


def singbox_dns(resolvers: list[DnsResolver], *, fake_ip: bool) -> dict[str, Any] | None:
    if not resolvers:
        return None
    servers: list[dict[str, Any]] = []
    for index, resolver in enumerate(resolvers, 1):
        kind = (
            "https"
            if isinstance(resolver, DohResolver)
            else "tls"
            if isinstance(resolver, DotResolver)
            else resolver.protocol
        )
        server: dict[str, Any] = {
            "tag": f"resolver-{index}",
            "type": kind,
            "server": resolver.address,
            "server_port": resolver.port,
        }
        if isinstance(resolver, DohResolver):
            server["path"] = resolver.path
        servers.append(server)
    dns: dict[str, Any] = {"servers": servers, "final": "resolver-1"}
    if fake_ip:
        servers.append(
            {"tag": "policy-fakeip", "type": "fakeip", "inet4_range": "198.18.0.0/15", "inet6_range": "fc00::/18"}
        )
        dns["rules"] = [{"query_type": ["A", "AAAA"], "server": "policy-fakeip"}]
    return dns
