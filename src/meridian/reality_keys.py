"""Reality x25519 keypair generation on a target server."""

from __future__ import annotations

import logging

from meridian.core.errors import MeridianError
from meridian.ssh import ServerConnection

logger = logging.getLogger(__name__)


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
