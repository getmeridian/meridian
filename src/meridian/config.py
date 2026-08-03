"""Paths, URLs, and constants."""

from __future__ import annotations

import os
from pathlib import Path

MERIDIAN_HOME = Path(os.environ.get("MERIDIAN_HOME", Path.home() / ".meridian"))
CLUSTER_CONFIG = MERIDIAN_HOME / "cluster.yml"
CLUSTER_BACKUP = MERIDIAN_HOME / "cluster.yml.bak"
CACHE_DIR = MERIDIAN_HOME / "cache"
SERVER_PROFILES_FILE = MERIDIAN_HOME / "servers.json"
SETUP_DRAFT_FILE = MERIDIAN_HOME / "setup.json"
MERIDIAN_SSH_KEY_FILE = MERIDIAN_HOME / "ssh" / "meridian_ed25519"
SERVER_CREDS_DIR = Path("/etc/meridian")
SERVER_NODE_CONFIG = SERVER_CREDS_DIR / "node.yml"  # server-side identity

DEFAULT_SNI = "www.microsoft.com"  # mirrored in meridian.core.defaults for core isolation
DEFAULT_FINGERPRINT = "chrome"
ACME_SERVER = os.environ.get("MERIDIAN_ACME_SERVER", "letsencrypt").strip() or "letsencrypt"

# Remnawave panel + node
REMNAWAVE_BACKEND_IMAGE = "remnawave/backend:2.8.0"
REMNAWAVE_NODE_IMAGE = "remnawave/node:2.8.0"
REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE = "remnawave/subscription-page:7.2.6"
REMNAWAVE_PANEL_PORT = 3000  # internal port, nginx reverse-proxied
REMNAWAVE_NODE_API_PORT = 3010  # node API port (panel→node mTLS communication)
REMNAWAVE_SUBSCRIPTION_PAGE_PORT = 3020  # host port (internal 3010 remapped to avoid node API conflict)
REMNAWAVE_PANEL_DIR = "/opt/remnawave"
REMNAWAVE_NODE_DIR = "/opt/remnanode"

_CONNECT_TEST_URL_OVERRIDE = os.environ.get("MERIDIAN_CONNECT_TEST_URL", "").strip()
CONNECT_TEST_URLS = (
    (_CONNECT_TEST_URL_OVERRIDE,)
    if _CONNECT_TEST_URL_OVERRIDE
    else (
        "https://ifconfig.me/ip",
        "https://api.ipify.org",
        "https://icanhazip.com",
    )
)
# Singular alias retained for callers that expose the configured primary observer.
CONNECT_TEST_URL = CONNECT_TEST_URLS[0]
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
XRAY_VERSION = "26.6.27"  # matches xray bundled in remnawave/node:2.8.0
XRAY_GITHUB_URL = "https://github.com/XTLS/Xray-core/releases/download"
XRAY_ASSET_MAP: dict[tuple[str, str], str] = {
    ("Darwin", "arm64"): "Xray-macos-arm64-v8a.zip",
    ("Darwin", "x86_64"): "Xray-macos-64.zip",
    ("Linux", "x86_64"): "Xray-linux-64.zip",
    ("Linux", "aarch64"): "Xray-linux-arm64-v8a.zip",
}

# RealiTLScanner (remote SNI discovery helper)
REALITL_SCANNER_VERSION = "0.2.3"
REALITL_SCANNER_GITHUB_URL = "https://github.com/XTLS/RealiTLScanner/releases/download"
REALITL_SCANNER_ASSETS: dict[str, tuple[str, str]] = {
    "x86_64": (
        "RealiTLScanner-linux-amd64",
        "a55595446de9f1c2e6c5c3cd766a7320a11115947df48f101749bb62c8055592",
    ),
    "aarch64": (
        "RealiTLScanner-linux-arm64",
        "27bdd3e53d4391c66c8df3391d3c3fb5eb2dc356125f2fb33ac58fcaaf8f88b3",
    ),
}


def is_ip(s: str) -> bool:
    """Check if string is a valid IPv4 or IPv6 address."""
    import ipaddress

    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False
