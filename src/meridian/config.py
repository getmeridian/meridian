"""Paths, URLs, and constants."""

from __future__ import annotations

import os
from pathlib import Path

MERIDIAN_HOME = Path(os.environ.get("MERIDIAN_HOME", Path.home() / ".meridian"))
CREDS_BASE = MERIDIAN_HOME / "credentials"
CACHE_DIR = MERIDIAN_HOME / "cache"
SERVERS_FILE = MERIDIAN_HOME / "servers"
SERVER_CREDS_DIR = Path("/etc/meridian")

DEFAULT_SNI = "www.cloudflare.com"
DEFAULT_FINGERPRINT = "chrome"
DEFAULT_PANEL_PORT = 2053
ACME_SERVER = os.environ.get("MERIDIAN_ACME_SERVER", "letsencrypt").strip() or "letsencrypt"
CONNECT_TEST_URL = os.environ.get("MERIDIAN_CONNECT_TEST_URL", "").strip()
CONNECT_TEST_URL_IPV4 = "https://api.ipify.org"
CONNECT_TEST_URL_IPV6 = "https://api6.ipify.org"
DISABLE_UPDATE_CHECK = os.environ.get("MERIDIAN_DISABLE_UPDATE_CHECK", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

PYPI_PACKAGE = "meridian-vpn"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PYPI_PACKAGE}/json"
GITHUB_REPO = "https://github.com/uburuntu/meridian"
GITHUB_ISSUES = f"{GITHUB_REPO}/issues"
WEBSITE_URL = "https://getmeridian.org"

# Update throttle (seconds)
UPDATE_CHECK_INTERVAL = 60

# Relay (Realm TCP relay)
REALM_VERSION = "2.9.3"
REALM_GITHUB_URL = "https://github.com/zhboner/realm/releases/download"
RELAY_SERVICE_NAME = "meridian-relay"

# SHA256 digests for Realm tarball verification (keyed by target triple)
REALM_SHA256: dict[str, str] = {
    "x86_64-unknown-linux-gnu": "2eba86f1a1e47c1bfe9d6fd682ef8667bd05e57c3aeb0ec37806aabe2ce74a0c",
    "aarch64-unknown-linux-gnu": "9937daacdcdfcac9fd78d25819f2de0a5c3357c2c49e686679d812343ab8661e",
}
RELAY_CONFIG_PATH = "/etc/meridian/realm.toml"

# Xray client binary (for connection verification)
XRAY_VERSION = "26.2.6"  # matches xray bundled in 3x-ui 2.8.11
XRAY_GITHUB_URL = "https://github.com/XTLS/Xray-core/releases/download"
XRAY_ASSET_MAP: dict[tuple[str, str], str] = {
    ("Darwin", "arm64"): "Xray-macos-arm64-v8a.zip",
    ("Darwin", "x86_64"): "Xray-macos-64.zip",
    ("Linux", "x86_64"): "Xray-linux-64.zip",
    ("Linux", "aarch64"): "Xray-linux-arm64-v8a.zip",
}


def is_ipv4(s: str) -> bool:
    """Check if string looks like an IPv4 address."""
    parts = s.split(".")
    if len(parts) != 4:
        return False
    return all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def is_ip(s: str) -> bool:
    """Check if string is a valid IPv4 or IPv6 address."""
    import ipaddress

    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def sanitize_ip_for_path(ip: str) -> str:
    """Convert an IP address to a filesystem-safe directory name.

    IPv4 addresses are returned unchanged (backward compatible).
    IPv6 colons are replaced with hyphens: 2001:db8::1 -> 2001-db8--1
    """
    if ":" in ip:
        return ip.replace(":", "-")
    return ip


def creds_dir_for(ip: str, *, local_mode: bool) -> Path:
    """Determine the local credential directory for a server.

    Root in local mode reads/writes /etc/meridian directly.
    Everything else uses ~/.meridian/credentials/<ip>.
    """
    if local_mode and os.geteuid() == 0:
        return SERVER_CREDS_DIR
    return CREDS_BASE / sanitize_ip_for_path(ip)
