"""Xray configuration builder and Reality keypair generation.

Protocol-level domain logic extracted from panel_bootstrap.py.
Builds Xray inbound definitions (Reality, XHTTP, WSS) and generates
x25519 keypairs for the Reality protocol.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from meridian.console import fail, info
from meridian.ssh import ServerConnection


@dataclass(frozen=True)
class XrayConfigResult:
    """Result of building an Xray configuration."""

    config: dict[str, Any]
    reality_public_key: str
    reality_short_id: str
    reality_private_key: str


def generate_reality_keypair(conn: ServerConnection) -> tuple[str, str]:
    """Generate x25519 keypair for Reality using tools available on the server.

    Tries multiple sources: Xray in any running container, xray on host.
    Returns (private_key, public_key) as base64 strings.
    """
    # Try various Xray binaries that might be available
    cmds = [
        "docker exec remnawave-node rw-core x25519 2>/dev/null",
        "docker exec 3x-ui /app/bin/xray-linux-amd64 x25519 2>/dev/null",
        "xray x25519 2>/dev/null",
    ]
    for cmd in cmds:
        result = conn.run(cmd, timeout=15)
        if result.returncode != 0 or not result.stdout.strip():
            continue
        private_key = ""
        public_key = ""
        for raw_line in result.stdout.strip().splitlines():
            ln = raw_line.strip()
            low = ln.lower()
            if "private" in low and ":" in ln:
                private_key = ln.split(":", 1)[1].strip().strip('"')
            elif ("public" in low or "password" in low) and ":" in ln:
                if "hash" not in low:
                    public_key = ln.split(":", 1)[1].strip().strip('"')
        if private_key and public_key:
            return private_key, public_key

    # Last resort: download a temporary Xray binary
    info("Downloading Xray binary for key generation...")
    dl_result = conn.run(
        "ARCH=$(uname -m); "
        'case "$ARCH" in '
        "aarch64|arm64) XRAY_ARCH=arm64-v8a ;; "
        "*) XRAY_ARCH=64 ;; "
        "esac; "
        'curl -sL "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-${XRAY_ARCH}.zip"'
        " -o /tmp/xray.zip && cd /tmp && unzip -qo xray.zip xray && chmod +x xray"
        " && /tmp/xray x25519 && rm -f /tmp/xray /tmp/xray.zip",
        timeout=60,
    )
    if dl_result.returncode == 0:
        for raw_line in dl_result.stdout.strip().splitlines():
            ln = raw_line.strip()
            low = ln.lower()
            if "private" in low and ":" in ln:
                private_key = ln.split(":", 1)[1].strip().strip('"')
            elif ("public" in low or "password" in low) and ":" in ln:
                if "hash" not in low:
                    public_key = ln.split(":", 1)[1].strip().strip('"')
        if private_key and public_key:
            return private_key, public_key

    fail(
        "Could not generate Reality x25519 keypair",
        hint="Install xray on the server or ensure Docker is running",
        hint_type="system",
    )
    return "", ""  # unreachable


def build_xray_config(
    conn: ServerConnection | None,
    sni: str,
    reality_port: int,
    xhttp_port: int,
    wss_port: int,
    domain: str,
    *,
    pq: bool,
    geo_block: bool,
    warp: bool = False,
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
            fail("Cannot generate Reality keys without SSH connection", hint_type="bug")
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
    if pq:
        rs = reality_inbound["streamSettings"]
        if isinstance(rs, dict):
            rs_inner = rs.get("realitySettings")
            if isinstance(rs_inner, dict):
                rs_inner["fingerprint"] = "chrome"
    config["inbounds"].append(reality_inbound)

    # XHTTP inbound (enhanced stealth -- behind nginx reverse proxy)
    xhttp_stream: dict[str, Any] = {
        "network": "xhttp",
        "security": "none",
    }
    if xhttp_path:
        xhttp_stream["xhttpSettings"] = {"path": f"/{xhttp_path}"}
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

    return XrayConfigResult(
        config=config,
        reality_public_key=public_key,
        reality_short_id=short_id,
        reality_private_key=private_key,
    )
