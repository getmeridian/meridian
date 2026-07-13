"""Paths, URLs, and constants."""

from __future__ import annotations

import os
from pathlib import Path

MERIDIAN_HOME = Path(os.environ.get("MERIDIAN_HOME", Path.home() / ".meridian"))
CLUSTER_CONFIG = MERIDIAN_HOME / "cluster.yml"
CLUSTER_BACKUP = MERIDIAN_HOME / "cluster.yml.bak"
CACHE_DIR = MERIDIAN_HOME / "cache"
SERVER_PROFILES_FILE = MERIDIAN_HOME / "servers.json"
MERIDIAN_SSH_KEY_FILE = MERIDIAN_HOME / "ssh" / "meridian_ed25519"
SERVER_CREDS_DIR = Path("/etc/meridian")
SERVER_NODE_CONFIG = SERVER_CREDS_DIR / "node.yml"  # server-side identity

DEFAULT_SNI = "www.microsoft.com"  # mirrored in meridian.core.defaults for core isolation
DEFAULT_FINGERPRINT = "chrome"
ACME_SERVER = os.environ.get("MERIDIAN_ACME_SERVER", "letsencrypt").strip() or "letsencrypt"

# Remnawave panel + node
REMNAWAVE_BACKEND_IMAGE = "remnawave/backend:2.7.4"
REMNAWAVE_NODE_IMAGE = "remnawave/node:2.7.0"
REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE = "remnawave/subscription-page:7.2.1"
REMNAWAVE_PANEL_PORT = 3000  # internal port, nginx reverse-proxied
REMNAWAVE_NODE_API_PORT = 3010  # node API port (panel→node mTLS communication)
REMNAWAVE_SUBSCRIPTION_PAGE_PORT = 3020  # host port (internal 3010 remapped to avoid node API conflict)
REMNAWAVE_PANEL_DIR = "/opt/remnawave"
REMNAWAVE_NODE_DIR = "/opt/remnanode"

CONNECT_TEST_URL = os.environ.get("MERIDIAN_CONNECT_TEST_URL", "https://ifconfig.me").strip() or "https://ifconfig.me"
DISABLE_UPDATE_CHECK = os.environ.get("MERIDIAN_DISABLE_UPDATE_CHECK", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

PYPI_PACKAGE = "meridian-vpn"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PYPI_PACKAGE}/json"
GITHUB_REPO = "https://github.com/getmeridian/meridian"

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
XRAY_VERSION = "26.3.27"  # matches xray bundled in remnawave/node:2.7.0
XRAY_GITHUB_URL = "https://github.com/XTLS/Xray-core/releases/download"
XRAY_ASSET_MAP: dict[tuple[str, str], str] = {
    ("Darwin", "arm64"): "Xray-macos-arm64-v8a.zip",
    ("Darwin", "x86_64"): "Xray-macos-64.zip",
    ("Linux", "x86_64"): "Xray-linux-64.zip",
    ("Linux", "aarch64"): "Xray-linux-arm64-v8a.zip",
}


def is_ip(s: str) -> bool:
    """Check if string is a valid IPv4 or IPv6 address."""
    import ipaddress

    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False
