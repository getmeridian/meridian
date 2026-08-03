"""Legacy local Xray config builders retained for compatibility tooling."""

from __future__ import annotations

import socket

from meridian.cluster import ClusterConfig
from meridian.config import DEFAULT_FINGERPRINT, DEFAULT_SNI


def _socks_inbound(port: int) -> dict:
    return {
        "protocol": "socks",
        "listen": "127.0.0.1",
        "port": port,
        "settings": {"auth": "noauth"},
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_reality_config(
    socks_port: int,
    server_ip: str,
    uuid: str,
    sni: str,
    public_key: str,
    short_id: str,
    encryption: str = "none",
    fingerprint: str = DEFAULT_FINGERPRINT,
    server_port: int = 443,
) -> dict:
    """Build an Xray client config for VLESS+Reality."""
    return {
        "log": {"loglevel": "none"},
        "inbounds": [_socks_inbound(socks_port)],
        "outbounds": [
            {
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": server_ip,
                            "port": server_port,
                            "users": [
                                {
                                    "id": uuid,
                                    "encryption": encryption,
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
                        "publicKey": public_key,
                        "fingerprint": fingerprint,
                        "serverName": sni,
                        "shortId": short_id,
                    },
                },
            }
        ],
    }


def build_xhttp_config(
    socks_port: int,
    host: str,
    uuid: str,
    xhttp_path: str,
    fingerprint: str = DEFAULT_FINGERPRINT,
    server_port: int = 443,
) -> dict:
    """Build an Xray client config for VLESS+XHTTP over TLS."""
    return {
        "log": {"loglevel": "none"},
        "inbounds": [_socks_inbound(socks_port)],
        "outbounds": [
            {
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": host,
                            "port": server_port,
                            "users": [{"id": uuid, "encryption": "none"}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "xhttp",
                    "security": "tls",
                    "tlsSettings": {
                        "serverName": host,
                        "fingerprint": fingerprint,
                    },
                    "xhttpSettings": {"path": f"/{xhttp_path}"},
                },
            }
        ],
    }


def build_wss_config(
    socks_port: int,
    domain: str,
    uuid: str,
    ws_path: str,
) -> dict:
    """Build an Xray client config for VLESS+WSS over TLS."""
    return {
        "log": {"loglevel": "none"},
        "inbounds": [_socks_inbound(socks_port)],
        "outbounds": [
            {
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": domain,
                            "port": 443,
                            "users": [{"id": uuid, "encryption": "none"}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "ws",
                    "security": "tls",
                    "tlsSettings": {"serverName": domain},
                    "wsSettings": {
                        "path": f"/{ws_path}",
                        "headers": {"Host": domain},
                    },
                },
            }
        ],
    }


def build_test_configs_from_cluster(
    cluster: ClusterConfig,
    node_ip: str,
    *,
    uuid: str = "",
) -> list[tuple[str, dict, bool]]:
    """Build legacy local test configs from persisted node metadata."""
    node = cluster.find_node(node_ip)
    if node is None:
        return []

    ip = node.ip
    sni = node.sni or DEFAULT_SNI
    domain = node.domain or ""
    public_key = node.reality_public_key or ""
    short_id = node.reality_short_id or ""
    xhttp_path = node.xhttp_path or ""
    ws_path = node.ws_path or ""
    warp = getattr(node, "warp", False)
    test_uuid = uuid or "00000000-0000-0000-0000-000000000000"
    configs: list[tuple[str, dict, bool]] = []

    if public_key:
        configs.append(
            (
                "Reality (TCP)",
                build_reality_config(_free_port(), ip, test_uuid, sni, public_key, short_id),
                not warp,
            )
        )
    if xhttp_path:
        host = domain or ip
        configs.append(
            (
                "XHTTP",
                build_xhttp_config(_free_port(), host, test_uuid, xhttp_path),
                not domain and not warp,
            )
        )
    if domain and ws_path:
        configs.append(("WSS (CDN)", build_wss_config(_free_port(), domain, test_uuid, ws_path), False))

    for relay in cluster.relays:
        if relay.exit_node_ip and relay.exit_node_ip != ip:
            continue
        relay_label = relay.name or relay.ip
        relay_sni = relay.sni or sni
        if public_key:
            configs.append(
                (
                    f"Reality via {relay_label}",
                    build_reality_config(
                        _free_port(),
                        relay.ip,
                        test_uuid,
                        relay_sni,
                        public_key,
                        short_id,
                        server_port=relay.port,
                    ),
                    not warp,
                )
            )
        if xhttp_path:
            xhttp_host = domain or ip
            config = build_xhttp_config(
                _free_port(),
                xhttp_host,
                test_uuid,
                xhttp_path,
                server_port=relay.port,
            )
            config["outbounds"][0]["settings"]["vnext"][0]["address"] = relay.ip
            configs.append((f"XHTTP via {relay_label}", config, not domain and not warp))
    return configs
