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

DEFAULT_SNI = "www.google.com"  # mirrored in meridian.core.defaults for core isolation
DEFAULT_FINGERPRINT = "chrome"
ACME_SERVER = os.environ.get("MERIDIAN_ACME_SERVER", "letsencrypt").strip() or "letsencrypt"

# Remnawave panel + node
# Tested tuple. Installed on fresh servers; never used to downgrade a newer
# tag the operator chose (see meridian.remnawave.versions).
REMNAWAVE_BACKEND_IMAGE = "remnawave/backend:3.4.5"
REMNAWAVE_NODE_IMAGE = "remnawave/node:3.4.2"
REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE = "remnawave/subscription-page:8.0.0"
REMNAWAVE_MIN_VERSION = "3.4.0"  # oldest panel API Meridian speaks
REMNAWAVE_TESTED_MAJOR = 3  # newer majors run with a warning
REMNAWAVE_PANEL_PORT = 3000  # internal port, nginx reverse-proxied
REMNAWAVE_NODE_API_PORT = 3010  # node API port (panel→node mTLS communication)
REMNAWAVE_SUBSCRIPTION_PAGE_PORT = 3020  # host port (internal 3010 remapped to avoid node API conflict)
REMNAWAVE_PANEL_DIR = "/opt/remnawave"
REMNAWAVE_NODE_DIR = "/opt/remnanode"

# Exit-IP observers are single-stack: a dual-stack exit would otherwise answer
# a dual-stack observer over IPv6 and fail the egress match against its IPv4.
_CONNECT_TEST_URL_OVERRIDE = os.environ.get("MERIDIAN_CONNECT_TEST_URL", "").strip()
CONNECT_TEST_URLS_IPV4 = (
    (_CONNECT_TEST_URL_OVERRIDE,)
    if _CONNECT_TEST_URL_OVERRIDE
    else (
        "https://api.ipify.org",
        "https://ipv4.icanhazip.com",
        "https://v4.ident.me",
    )
)
CONNECT_TEST_URLS_IPV6 = (
    (_CONNECT_TEST_URL_OVERRIDE,)
    if _CONNECT_TEST_URL_OVERRIDE
    else (
        "https://api6.ipify.org",
        "https://ipv6.icanhazip.com",
        "https://v6.ident.me",
    )
)
# Singular alias retained for callers that expose the configured primary observer.
CONNECT_TEST_URL = CONNECT_TEST_URLS_IPV4[0]
DISABLE_UPDATE_CHECK = os.environ.get("MERIDIAN_DISABLE_UPDATE_CHECK", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

PYPI_PACKAGE = "meridian-vpn"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PYPI_PACKAGE}/json"
GITHUB_REPO = "https://github.com/getmeridian/meridian"
BETA_REPORT_URL = f"{GITHUB_REPO}/issues/new?template=v4_beta.yml"

# Update throttle (seconds)
UPDATE_CHECK_INTERVAL = 60

# Primary-key fingerprints of third-party apt repositories Meridian adds
APT_KEY_FINGERPRINTS: dict[str, frozenset[str]] = {
    "docker": frozenset({"9DC858229FC7DD38854AE2D88D81803C0EBFCD88"}),
    "nginx": frozenset(
        {
            "573BFD6B3D8FBC641079A6ABABF5BD827BD9BF62",
            "8540A6F18833A80E9C1653A42FD21310B49F6B46",
            "9E9BE90EACBCDE69FE9B204CBCDCD8A38D88A2B3",
        }
    ),
    "cloudflare-warp": frozenset({"C068A2B5771775193CBE1F2F6E2DD2174FA1C3BA"}),
}

# acme.sh (TLS certificates) — the script is pinned and checksum-verified before it runs as root
ACME_SH_VERSION = "3.1.6"
ACME_SH_SHA256 = "c7d68b021cfd6380ea83a82962abde5b484779fee0b97d38681dfa1396bbc8d7"

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
XRAY_VERSION = "26.7.28"  # matches xray bundled in remnawave/node:3.4.2
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
