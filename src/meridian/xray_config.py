"""Xray configuration builder and Reality keypair generation.

Protocol-level domain logic extracted from panel_bootstrap.py.
Builds Xray inbound definitions (Reality, XHTTP, WSS) and generates
x25519 keypairs for the Reality protocol.
"""

from __future__ import annotations

import logging
import secrets
import shlex
from dataclasses import dataclass
from typing import Any

from meridian.core.errors import MeridianError
from meridian.ssh import ServerConnection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class XrayConfigResult:
    """Result of building an Xray configuration."""

    config: dict[str, Any]
    reality_public_key: str
    reality_short_id: str
    reality_private_key: str


def _parse_reality_key_output(output: str) -> tuple[str, str]:
    """Extract private/public x25519 values from Xray CLI output."""
    private_key = ""
    public_key = ""
    for raw_line in output.strip().splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if "private" in lowered and ":" in line:
            private_key = line.split(":", 1)[1].strip().strip('"')
        elif ("public" in lowered or "password" in lowered) and ":" in line and "hash" not in lowered:
            public_key = line.split(":", 1)[1].strip().strip('"')
    return private_key, public_key


def derive_reality_public_key(conn: ServerConnection, private_key: str) -> str:
    """Derive a Reality public key without rotating recovered key material."""
    quoted_private_key = shlex.quote(private_key)
    commands = [
        f"docker exec remnawave-node rw-core x25519 -i {quoted_private_key} 2>/dev/null",
        f"xray x25519 -i {quoted_private_key} 2>/dev/null",
    ]
    for command in commands:
        result = conn.run(command, timeout=15, sensitive=True)
        if result.returncode == 0:
            _, public_key = _parse_reality_key_output(result.stdout)
            if public_key:
                return public_key
    return ""


def generate_reality_keypair(conn: ServerConnection) -> tuple[str, str]:
    """Generate x25519 keypair for Reality using tools available on the server.

    Tries the Remnawave node container, then xray on the host.
    Returns (private_key, public_key) as base64 strings.
    """
    # Try various Xray binaries that might be available
    cmds = [
        "docker exec remnawave-node rw-core x25519 2>/dev/null",
        "xray x25519 2>/dev/null",
    ]
    for cmd in cmds:
        result = conn.run(cmd, timeout=15)
        if result.returncode != 0 or not result.stdout.strip():
            continue
        private_key, public_key = _parse_reality_key_output(result.stdout)
        if private_key and public_key:
            return private_key, public_key

    # Last resort: download a temporary Xray binary
    logger.info("Downloading Xray binary for key generation...")
    from meridian.config import XRAY_VERSION

    dl_result = conn.run(
        "ARCH=$(uname -m); "
        'case "$ARCH" in '
        "aarch64|arm64) XRAY_ARCH=arm64-v8a ;; "
        "*) XRAY_ARCH=64 ;; "
        "esac; "
        f'curl -sL "https://github.com/XTLS/Xray-core/releases/download/v{XRAY_VERSION}/Xray-linux-${{XRAY_ARCH}}.zip"'
        " -o /tmp/xray.zip && cd /tmp && unzip -qo xray.zip xray && chmod +x xray"
        " && /tmp/xray x25519 && rm -f /tmp/xray /tmp/xray.zip",
        timeout=60,
    )
    if dl_result.returncode == 0:
        private_key, public_key = _parse_reality_key_output(dl_result.stdout)
        if private_key and public_key:
            return private_key, public_key

    raise MeridianError(
        "Could not generate Reality x25519 keypair",
        hint="Install xray on the server or ensure Docker is running",
        category="system",
    )


def build_xray_config(
    conn: ServerConnection | None,
    sni: str,
    reality_port: int,
    xhttp_port: int,
    wss_port: int,
    domain: str,
    *,
    geo_block: bool,
    warp: bool = False,
    hysteria2: bool = True,
    xhttp_path: str = "",
    ws_path: str = "",
    existing_private_key: str = "",
    existing_public_key: str = "",
    existing_short_id: str = "",
) -> XrayConfigResult:
    """Build the Xray configuration for a Remnawave config profile.

    This defines the inbounds that the node will run. The actual Xray
    config is managed by Remnawave, but we provide the template.

    If existing_private_key/public_key/short_id are provided, reuses
    them instead of generating new Reality keys (preserves client configs
    on redeploy). When provided, conn may be None.
    """
    # Base config with DNS and routing
    config: dict = {
        "log": {"loglevel": "warning"},
        "dns": {
            "servers": [
                {"address": "https://dns.google/dns-query", "domains": ["geosite:geolocation-!cn"]},
                "8.8.8.8",
            ],
        },
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [],
        },
        "inbounds": [],
    }

    # Geo-blocking rules
    if geo_block:
        config["routing"]["rules"].extend(
            [
                {"type": "field", "domain": ["geosite:category-ru"], "outboundTag": "block"},
                {"type": "field", "ip": ["geoip:ru"], "outboundTag": "block"},
            ]
        )

    # Block private IPs (prevent probing internal network)
    config["routing"]["rules"].append({"type": "field", "ip": ["geoip:private"], "outboundTag": "block"})

    # Outbounds
    outbounds: list[dict] = []
    if warp:
        from meridian.provision.warp import WARP_PROXY_PORT

        outbounds.append(
            {
                "tag": "warp",
                "protocol": "socks",
                "settings": {"servers": [{"address": "127.0.0.1", "port": WARP_PROXY_PORT}]},
            }
        )
    outbounds.append({"tag": "direct", "protocol": "freedom"})
    outbounds.append({"tag": "block", "protocol": "blackhole"})
    config["outbounds"] = outbounds

    # Reality inbound (primary -- always present)
    # Reuse existing keys on redeploy, generate fresh on first deploy
    if existing_private_key and existing_public_key and existing_short_id:
        private_key = existing_private_key
        public_key = existing_public_key
        short_id = existing_short_id
    else:
        if conn is None:
            raise MeridianError(
                "Cannot generate Reality keys without SSH connection",
                category="bug",
            )
        private_key, public_key = generate_reality_keypair(conn)
        short_id = secrets.token_hex(4)  # 8-char hex

    reality_inbound = {
        "tag": "vless-reality",
        "protocol": "vless",
        "listen": "127.0.0.1",
        "port": reality_port,
        "settings": {
            "clients": [],
            "decryption": "none",
        },
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "dest": f"{sni}:443",
                "serverNames": [sni],
                "privateKey": private_key,
                "shortIds": [short_id],
                "fingerprint": "chrome",
            },
        },
    }
    config["inbounds"].append(reality_inbound)

    # XHTTP inbound (enhanced stealth -- behind nginx reverse proxy)
    # mode=packet-up: nginx proxy_pass cannot transport stream-up/stream-one
    # gRPC framing. Explicit packet-up prevents clients from attempting and
    # failing stream-up, which would silently degrade performance.
    xhttp_stream: dict[str, Any] = {
        "network": "xhttp",
        "security": "none",
    }
    xhttp_settings: dict[str, Any] = {"mode": "packet-up"}
    if xhttp_path:
        xhttp_settings["path"] = f"/{xhttp_path}"
    xhttp_stream["xhttpSettings"] = xhttp_settings
    xhttp_inbound = {
        "tag": "vless-xhttp",
        "protocol": "vless",
        "listen": "127.0.0.1",
        "port": xhttp_port,
        "settings": {
            "clients": [],
            "decryption": "none",
        },
        "streamSettings": xhttp_stream,
    }
    config["inbounds"].append(xhttp_inbound)

    # WSS inbound (CDN fallback -- domain mode only)
    if domain:
        ws_stream: dict[str, Any] = {
            "network": "ws",
            "security": "none",
        }
        if ws_path:
            ws_stream["wsSettings"] = {"path": f"/{ws_path}"}
        wss_inbound = {
            "tag": "vless-wss",
            "protocol": "vless",
            "listen": "127.0.0.1",
            "port": wss_port,
            "settings": {
                "clients": [],
                "decryption": "none",
            },
            "streamSettings": ws_stream,
        }
        config["inbounds"].append(wss_inbound)

    # Hysteria2 inbound (UDP/443 fallback, ordered after TCP transports)
    # Coexists with TCP/443 (different L4 protocol). Handles its own TLS
    # using the same acme.sh certificates as nginx. Listens on all
    # interfaces since there is no nginx proxy for UDP traffic.
    # The node container runs with network_mode:host so binding to :: works.
    if hysteria2:
        hy2_inbound = {
            "tag": "hysteria2",
            "protocol": "hysteria",
            "listen": "0.0.0.0",
            "port": 443,
            "settings": {
                "clients": [],
                "version": 2,
            },
            "streamSettings": {
                "network": "hysteria",
                "security": "tls",
                "hysteriaSettings": {
                    "version": 2,
                },
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
        config["inbounds"].append(hy2_inbound)

    return XrayConfigResult(
        config=config,
        reality_public_key=public_key,
        reality_short_id=short_id,
        reality_private_key=private_key,
    )
