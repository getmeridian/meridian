"""Render one reviewed V4 workload into a Remnawave Xray profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from meridian.cluster import RealityKeyBinding
from meridian.compiler.models import (
    ConfigProfilePayload,
    InboundPayload,
    ServiceOutboundSpec,
    WorkloadRouteSpec,
)
from meridian.core.errors import MeridianError
from meridian.provision.warp import WARP_PROXY_PORT


class WorkloadConfigError(MeridianError):
    """A reviewed workload cannot be rendered without rotating or guessing state."""


@dataclass(frozen=True)
class ServiceRouteCredential:
    """Runtime-only credentials for one reviewed gateway edge."""

    address: str
    vless_uuid: str
    public_key: str
    short_id: str


def render_workload_config(
    profile: ConfigProfilePayload,
    *,
    reality_keys: RealityKeyBinding | None,
    service_credentials: dict[str, ServiceRouteCredential] | None = None,
) -> dict[str, Any]:
    """Render exactly the protocols and egress policy reviewed in a Profile payload."""
    inbounds = [_render_inbound(inbound, reality_keys=reality_keys) for inbound in profile.inbounds]
    credentials = service_credentials or {}
    expected_edges = {edge.edge_id for edge in profile.service_outbounds}
    if set(credentials) != expected_edges:
        missing = sorted(expected_edges - set(credentials))
        extra = sorted(set(credentials) - expected_edges)
        raise WorkloadConfigError(
            f"Service-route credentials do not match {profile.workload_id}: missing={missing}, extra={extra}."
        )
    _validate_service_outbound_selectors(profile)
    outbounds: list[dict[str, Any]] = [
        _render_service_outbound(edge, credentials[edge.edge_id]) for edge in profile.service_outbounds
    ]
    if "warp" in profile.outbound_tags:
        outbounds.append(
            {
                "tag": "warp",
                "protocol": "socks",
                "settings": {
                    "servers": [
                        {
                            "address": "127.0.0.1",
                            "port": WARP_PROXY_PORT,
                        }
                    ]
                },
            }
        )
    outbounds.extend(
        [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ]
    )
    routing: dict[str, Any] = {
        "domainStrategy": "IPIfNonMatch",
        "rules": [
            {
                "type": "field",
                "ip": ["geoip:private"],
                "outboundTag": "block",
            }
        ],
    }
    for rule in profile.routing_rules:
        routing["rules"].extend(_render_route(rule))
    if profile.egress_balancers:
        routing["balancers"] = [
            {
                "tag": balancer.tag,
                "selector": balancer.outbound_tags,
                "strategy": {"type": "leastPing"},
                "fallbackTag": balancer.fallback_tag,
            }
            for balancer in profile.egress_balancers
        ]
    config: dict[str, Any] = {
        "log": {"loglevel": "warning"},
        "dns": {
            "servers": [
                {
                    "address": "https://dns.google/dns-query",
                    "domains": ["geosite:geolocation-!cn"],
                },
                "8.8.8.8",
            ]
        },
        "routing": routing,
        "inbounds": inbounds,
        "outbounds": outbounds,
    }
    if profile.egress_balancers:
        probe_urls = {balancer.probe_url for balancer in profile.egress_balancers}
        if len(probe_urls) != 1:
            raise WorkloadConfigError("One gateway Profile cannot use multiple observatory probe URLs.")
        config["observatory"] = {
            "subjectSelector": sorted(
                {outbound_tag for balancer in profile.egress_balancers for outbound_tag in balancer.outbound_tags}
            ),
            "probeUrl": next(iter(probe_urls)),
            "probeInterval": "10s",
            "enableConcurrency": True,
        }
    return config


def _render_inbound(
    inbound: InboundPayload,
    *,
    reality_keys: RealityKeyBinding | None,
) -> dict[str, Any]:
    listen_address = inbound.listen_address or ("0.0.0.0" if inbound.protocol == "hysteria2" else "127.0.0.1")
    if inbound.protocol == "reality":
        keys = _require_complete_reality_keys(reality_keys, inbound)
        return {
            "tag": inbound.tag,
            "protocol": "vless",
            "listen": listen_address,
            "port": inbound.listen_port,
            "settings": {"clients": [], "decryption": "none"},
            "streamSettings": {
                "network": "tcp",
                "security": "reality",
                "realitySettings": {
                    "dest": f"{inbound.reality_sni}:443",
                    "serverNames": inbound.reality_server_names or [inbound.reality_sni],
                    "privateKey": keys.private_key,
                    "shortIds": [keys.short_id],
                    "fingerprint": "chrome",
                },
            },
        }
    if inbound.protocol == "xhttp":
        settings: dict[str, Any] = {"mode": "packet-up"}
        if inbound.path:
            settings["path"] = _absolute_path(inbound.path)
        return {
            "tag": inbound.tag,
            "protocol": "vless",
            "listen": listen_address,
            "port": inbound.listen_port,
            "settings": {"clients": [], "decryption": "none"},
            "streamSettings": {
                "network": "xhttp",
                "security": "none",
                "xhttpSettings": settings,
            },
        }
    if inbound.protocol == "wss":
        stream: dict[str, Any] = {"network": "ws", "security": "none"}
        if inbound.path:
            stream["wsSettings"] = {"path": _absolute_path(inbound.path)}
        return {
            "tag": inbound.tag,
            "protocol": "vless",
            "listen": listen_address,
            "port": inbound.listen_port,
            "settings": {"clients": [], "decryption": "none"},
            "streamSettings": stream,
        }
    return {
        "tag": inbound.tag,
        "protocol": "hysteria",
        "listen": listen_address,
        "port": inbound.listen_port,
        "settings": {"clients": [], "version": 2},
        "streamSettings": {
            "network": "hysteria",
            "security": "tls",
            "hysteriaSettings": {"version": 2},
            "tlsSettings": {
                "certificates": [
                    {
                        "certificateFile": "/etc/ssl/meridian/fullchain.pem",
                        "keyFile": "/etc/ssl/meridian/key.pem",
                    }
                ],
                "alpn": ["h3"],
            },
        },
    }


def _render_service_outbound(
    edge: ServiceOutboundSpec,
    credential: ServiceRouteCredential,
) -> dict[str, Any]:
    if not all(
        [
            credential.address,
            credential.vless_uuid,
            credential.public_key,
            credential.short_id,
        ]
    ):
        raise WorkloadConfigError(f"Service-route credential {edge.edge_id} is incomplete.")
    return {
        "tag": edge.tag,
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": credential.address,
                    "port": edge.target_port,
                    "users": [
                        {
                            "id": credential.vless_uuid,
                            "encryption": "none",
                            "flow": "xtls-rprx-vision",
                        }
                    ],
                }
            ]
        },
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "fingerprint": "chrome",
                "serverName": edge.target_sni,
                "publicKey": credential.public_key,
                "shortId": credential.short_id,
            },
        },
    }


def _render_route(rule: WorkloadRouteSpec) -> list[dict[str, Any]]:
    target_key = "balancerTag" if rule.target_type == "balancer" else "outboundTag"
    target = {target_key: rule.target_tag}
    base: dict[str, Any] = {"type": "field", "ruleTag": rule.route_id, **target}
    if rule.match == "all":
        return [{**base, "network": "tcp,udp"}]
    if rule.match == "country":
        country_codes = [value.lower() for value in rule.match_values]
        return [{**base, "ip": [f"geoip:{country}" for country in country_codes]}]
    if rule.match == "domain":
        return [{**base, "domain": [f"domain:{value}" for value in rule.match_values]}]
    if rule.match == "ip":
        return [{**base, "ip": rule.match_values}]
    return [{**base, "network": ",".join(rule.match_values)}]


def _validate_service_outbound_selectors(profile: ConfigProfilePayload) -> None:
    tags = [outbound.tag for outbound in profile.service_outbounds]
    for index, tag in enumerate(tags):
        for other in tags[index + 1 :]:
            if tag.startswith(other) or other.startswith(tag):
                raise WorkloadConfigError(
                    "Service Outbound tags must be prefix-free because Xray selectors use prefix matching: "
                    f"{tag!r} overlaps {other!r}."
                )
    for balancer in profile.egress_balancers:
        for selector in balancer.outbound_tags:
            matches = [tag for tag in tags if tag.startswith(selector)]
            if matches != [selector]:
                raise WorkloadConfigError(
                    f"Balancer {balancer.pool_id} selector {selector!r} must identify exactly one service Outbound."
                )


def _require_complete_reality_keys(
    keys: RealityKeyBinding | None,
    inbound: InboundPayload,
) -> RealityKeyBinding:
    if keys is None or not keys.public_key or not keys.private_key or not keys.short_id:
        raise WorkloadConfigError(
            f"Reality key material is incomplete for {inbound.workload_ref}/{inbound.tag}.",
            hint="Recover the complete key triple before applying; Meridian will not rotate partial state.",
        )
    return keys


def _absolute_path(path: str) -> str:
    return path if path.startswith("/") else f"/{path}"
