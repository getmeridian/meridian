"""Render one reviewed V4 workload into a Remnawave Xray profile."""

from __future__ import annotations

from typing import Any

from meridian.cluster import RealityKeyBinding
from meridian.compiler.models import ConfigProfilePayload, InboundPayload
from meridian.core.errors import MeridianError
from meridian.provision.warp import WARP_PROXY_PORT


class WorkloadConfigError(MeridianError):
    """A reviewed workload cannot be rendered without rotating or guessing state."""


def render_workload_config(
    profile: ConfigProfilePayload,
    *,
    reality_keys: RealityKeyBinding | None,
) -> dict[str, Any]:
    """Render exactly the protocols and egress policy reviewed in a Profile payload."""
    inbounds = [
        _render_inbound(inbound, reality_keys=reality_keys)
        for inbound in profile.inbounds
    ]
    outbounds: list[dict[str, Any]] = []
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
    return {
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
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {
                    "type": "field",
                    "ip": ["geoip:private"],
                    "outboundTag": "block",
                }
            ],
        },
        "inbounds": inbounds,
        "outbounds": outbounds,
    }


def _render_inbound(
    inbound: InboundPayload,
    *,
    reality_keys: RealityKeyBinding | None,
) -> dict[str, Any]:
    if inbound.protocol == "reality":
        keys = _require_complete_reality_keys(reality_keys, inbound)
        return {
            "tag": inbound.tag,
            "protocol": "vless",
            "listen": "127.0.0.1",
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
            "listen": "127.0.0.1",
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
            "listen": "127.0.0.1",
            "port": inbound.listen_port,
            "settings": {"clients": [], "decryption": "none"},
            "streamSettings": stream,
        }
    return {
        "tag": inbound.tag,
        "protocol": "hysteria",
        "listen": "0.0.0.0",
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
