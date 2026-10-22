"""Render one reviewed workload into a Remnawave Xray profile."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from meridian.cluster import RealityKeyBinding
from meridian.compiler.delivery import ROUTE_ONLY_SNIFFING
from meridian.compiler.models import (
    ConfigProfilePayload,
    InboundPayload,
    ServiceOutboundSpec,
    WorkloadRouteSpec,
    certificate_directory,
)
from meridian.core.errors import MeridianError
from meridian.provision.warp import WARP_PROXY_PORT

REALITY_MIN_CLIENT_VERSION = "1.8.0"


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
    # Unmatched traffic leaves through the first outbound, so WARP leads only when it is the
    # default egress; an override-only WARP outbound goes after direct and block.
    warp_outbound = {
        "tag": "warp",
        "protocol": "socks",
        "settings": {"servers": [{"address": "127.0.0.1", "port": WARP_PROXY_PORT}]},
    }
    warp_default = profile.outbound_tags[:1] == ["warp"]
    if warp_default:
        outbounds.append(warp_outbound)
    outbounds.extend(
        [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ]
    )
    if not warp_default and "warp" in profile.outbound_tags:
        outbounds.append(warp_outbound)
    _validate_route_targets(profile, {outbound["tag"] for outbound in outbounds})
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
    # Reviewed routes (gateway rules, exit egress overrides) are explicit operator choices, so
    # they win over the blanket geo block; the private-range block above always comes first.
    for rule in profile.routing_rules:
        routing["rules"].extend(_render_route(rule))
    if profile.geo_block:
        routing["rules"].extend(
            [
                {"type": "field", "domain": ["geosite:category-ru"], "outboundTag": "block"},
                {"type": "field", "ip": ["geoip:ru"], "outboundTag": "block"},
            ]
        )
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
        # No access log: it would record every user's destinations on the server.
        # Remnawave reads only startup errors from the log; IP stats use the API.
        "log": {"loglevel": "warning", "access": "none"},
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
    rendered = _render_inbound_body(inbound, reality_keys=reality_keys)
    # Server-side sniffing feeds the geo-block domain rules for IP-only requests and
    # lets the panel's Torrent Blocker plugin see BitTorrent; it never rewrites targets.
    rendered["sniffing"] = {**ROUTE_ONLY_SNIFFING, "destOverride": list(ROUTE_ONLY_SNIFFING["destOverride"])}
    if inbound.trust_forwarded_ip:
        # Reality sits behind the 443 stream (PROXY protocol); XHTTP/WS behind an nginx TLS server.
        sockopt: dict[str, Any] = (
            {"acceptProxyProtocol": True}
            if inbound.protocol == "reality"
            else {"trustedXForwardedFor": ["X-Forwarded-For"]}
        )
        rendered["streamSettings"]["sockopt"] = sockopt
    return rendered


def _render_inbound_body(
    inbound: InboundPayload,
    *,
    reality_keys: RealityKeyBinding | None,
) -> dict[str, Any]:
    listen_address = inbound.listen_address or ("0.0.0.0" if inbound.protocol == "hysteria2" else "127.0.0.1")
    if inbound.protocol == "reality":
        keys = _require_complete_reality_keys(reality_keys, inbound)
        reality: dict[str, Any] = {
            # Self-steal falls back to the exit's own nginx; otherwise to the borrowed site.
            "dest": (
                f"127.0.0.1:{inbound.reality_dest_port}" if inbound.reality_dest_port else f"{inbound.reality_sni}:443"
            ),
            "serverNames": inbound.reality_server_names or [inbound.reality_sni],
            "privateKey": keys.private_key,
            "shortIds": [keys.short_id],
            "fingerprint": "chrome",
            # Xray 26.7.11+ defaults this to 26.3.27, which rejects Mihomo and
            # sing-box clients (they announce 1.8.x); REALITY began in 1.8.0.
            "minClientVer": REALITY_MIN_CLIENT_VERSION,
        }
        if inbound.reality_dest_proxy:
            reality["xver"] = 1
        return {
            "tag": inbound.tag,
            "protocol": "vless",
            "listen": listen_address,
            "port": inbound.listen_port,
            "settings": {"clients": [], "decryption": "none"},
            "streamSettings": {
                "network": "tcp",
                "security": "reality",
                "realitySettings": reality,
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
    if not inbound.tls_sni:
        raise WorkloadConfigError(f"Hysteria2 inbound {inbound.tag} has no TLS hostname.")
    keys = _require_complete_reality_keys(reality_keys, inbound)
    certificate_dir = certificate_directory(inbound.tls_sni)
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
                        "certificateFile": f"{certificate_dir}/fullchain.pem",
                        "keyFile": f"{certificate_dir}/key.pem",
                    }
                ],
                "alpn": ["h3"],
            },
            # Salamander turns QUIC (and its visible SNI) into random-looking UDP. Remnawave
            # copies the inbound's finalmask into every client format.
            "finalmask": {"udp": [{"type": "salamander", "settings": {"password": salamander_password(keys)}}]},
        },
    }


def salamander_password(keys: RealityKeyBinding) -> str:
    """Derive the workload's stable Hysteria2 obfuscation password from its Reality private key.

    Nothing new to store or rotate: the password follows the key triple, which never rotates
    and moves with ``node replace``. The HMAC reveals nothing about the private key.
    """
    digest = hmac.new(keys.private_key.encode("utf-8"), b"meridian-hysteria2-salamander", hashlib.sha256)
    return digest.hexdigest()[:32]


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
                "fingerprint": edge.target_fingerprint,
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
    if rule.match == "geosite":
        return [{**base, "domain": [f"geosite:{value}" for value in rule.match_values]}]
    if rule.match == "geoip":
        return [{**base, "ip": [f"geoip:{value}" for value in rule.match_values]}]
    if rule.match == "ip":
        return [{**base, "ip": rule.match_values}]
    return [{**base, "network": ",".join(rule.match_values)}]


def _validate_route_targets(profile: ConfigProfilePayload, outbound_tags: set[str]) -> None:
    for rule in profile.routing_rules:
        if rule.target_type == "outbound" and rule.target_tag not in outbound_tags:
            raise WorkloadConfigError(
                f"Route {rule.route_id} on {profile.workload_id} targets missing Outbound {rule.target_tag!r}."
            )


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
