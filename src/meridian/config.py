"""Paths, URLs, and constants."""

from __future__ import annotations

import os
from pathlib import Path

from meridian.contexts import CONTEXT_ENV, DEFAULT_CONTEXT, ContextError, ContextSelection, resolve_context

# Meridian 3.x keeps its state here; 4.x never writes to it.
LEGACY_HOME = Path.home() / ".meridian"
# Only Meridian 4 writes servers.json (3.x used `servers`), so it marks a 4.x home.
_V4_MARKER = "servers.json"


def default_home() -> Path:
    """Where Meridian 4 keeps local state when MERIDIAN_HOME is not set.

    The XDG config directory keeps 4.x clear of 3.x's ~/.meridian. A 4.x fleet already
    kept in ~/.meridian-v4 (the first beta guide) or ~/.meridian (4.0 development builds)
    stays where it is, so nobody loses access to a fleet or has to move folders.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    preferred = (Path(xdg) if os.path.isabs(xdg) else Path.home() / ".config") / "meridian"
    if preferred.exists():
        return preferred
    for earlier in (Path.home() / ".meridian-v4", LEGACY_HOME):
        if (earlier / _V4_MARKER).is_file():
            return earlier
    return preferred


def display_path(path: Path) -> str:
    """Show a path under the home directory as ~/..., the way people type it."""
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


# Per-install state: whoever runs Meridian, whichever fleet they work on.
# MERIDIAN_HOME pins one directory that is both the install home and the only fleet.
PINNED_HOME = bool(os.environ.get("MERIDIAN_HOME"))
INSTALL_HOME = Path(os.environ["MERIDIAN_HOME"]) if PINNED_HOME else default_home()
CACHE_DIR = INSTALL_HOME / "cache"
PREFERENCES_FILE = INSTALL_HOME / "preferences.json"
MERIDIAN_SSH_KEY_FILE = INSTALL_HOME / "ssh" / "meridian_ed25519"

# Per-fleet state, rebound by select_context(). Read these as `config.NAME` (or import them
# inside a function), never with a module-level `from meridian.config import NAME`: that copy
# would keep the fleet chosen at import time (tests/test_architecture.py refuses it).
CONTEXT = DEFAULT_CONTEXT
FLEET_HOME = INSTALL_HOME
HOME_DISPLAY = display_path(FLEET_HOME)
CLUSTER_CONFIG = FLEET_HOME / "cluster.yml"
CLUSTER_BACKUP = FLEET_HOME / "cluster.yml.bak"
SERVER_PROFILES_FILE = FLEET_HOME / "servers.json"
SETUP_DRAFT_FILE = FLEET_HOME / "setup.json"


def bind_context(selection: ContextSelection) -> None:
    """Point every per-fleet path at ``selection``'s directory."""
    global CONTEXT, FLEET_HOME, HOME_DISPLAY, CLUSTER_CONFIG, CLUSTER_BACKUP, SERVER_PROFILES_FILE, SETUP_DRAFT_FILE
    CONTEXT = selection.name
    FLEET_HOME = selection.home
    HOME_DISPLAY = display_path(selection.home)
    CLUSTER_CONFIG = selection.home / "cluster.yml"
    CLUSTER_BACKUP = selection.home / "cluster.yml.bak"
    SERVER_PROFILES_FILE = selection.home / "servers.json"
    SETUP_DRAFT_FILE = selection.home / "setup.json"


def select_context(flag: str = "") -> ContextSelection:
    """Resolve the fleet for this run (see ``contexts.resolve_context``) and bind its paths.

    Runs at import with no flag, so library callers start on the current context; the
    CLI root callback runs it again with ``--context`` before any command reads a path.
    """
    selection = resolve_context(
        INSTALL_HOME, pinned=PINNED_HOME, flag=flag, env=os.environ.get(CONTEXT_ENV, "").strip()
    )
    bind_context(selection)
    return selection


try:
    select_context()
except ContextError:
    pass  # stays on default; the CLI root callback re-resolves and reports the error

DEFAULT_SNI = "www.google.com"  # mirrored in meridian.core.defaults for core isolation
ACME_SERVER = os.environ.get("MERIDIAN_ACME_SERVER", "letsencrypt").strip() or "letsencrypt"

# Remnawave panel + node
# Tested tuple, pinned to multi-arch (amd64 + arm64) index digests so a
# re-pushed tag cannot change what a fresh server runs. Remnawave pins are
# installed on fresh servers and never used to downgrade a newer tag the
# operator chose (see meridian.remnawave.versions); PostgreSQL and Valkey are
# Meridian-owned and always rendered as pinned. Move the three Remnawave pins
# together, then refresh every digest with scripts/pin_image_digests.py.
REMNAWAVE_BACKEND_IMAGE = (
    "remnawave/backend:3.4.5@sha256:b16d724b90fd7c9fec2df04bd28938a671cafc62894105068e11550ee3449c56"
)
REMNAWAVE_NODE_IMAGE = "remnawave/node:3.4.2@sha256:1f97485b4bc7e4944f1ae95cc57d176376813b0022e9567b705f384f1a2e909d"
REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE = (
    "remnawave/subscription-page:8.0.0@sha256:04e8d479afb3598024e4018e9e15cd7fe879938250090a690ba39f1ee91b79ac"
)
REMNAWAVE_POSTGRES_IMAGE = (
    "postgres:17.11-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24"
)
REMNAWAVE_VALKEY_IMAGE = (
    "valkey/valkey:9.1.2-alpine@sha256:48332870af354a799964c0012ae1194a0bf2bf894eb508f945810596dc2d8d11"
)
REMNAWAVE_MIN_VERSION = "3.4.0"  # oldest panel API Meridian speaks
REMNAWAVE_TESTED_MAJOR = 3  # newer majors run with a warning
REMNAWAVE_PANEL_PORT = 3000  # internal port, nginx reverse-proxied
REMNAWAVE_NODE_API_PORT = 3010  # node API port (panel→node mTLS communication)
REMNAWAVE_SUBSCRIPTION_PAGE_PORT = 3020  # host port (internal 3010 remapped to avoid node API conflict)
# Runtime directories and container names live in meridian.host_runtime.

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
REALM_VERSION = "2.9.6"
REALM_GITHUB_URL = "https://github.com/zhboner/realm/releases/download"

# SHA256 digests for Realm tarball verification (keyed by target triple). The static
# musl builds run on any glibc; upstream's gnu build for 2.9.6 needs glibc 2.38.
REALM_SHA256: dict[str, str] = {
    "x86_64-unknown-linux-musl": "b1cc335547bea8bb2a88178bef12ec7f2363e36200e7ea1d4e1e67627929bf65",
    "aarch64-unknown-linux-musl": "f4c0318dd86854da483dcb7645b4f39cae2cc3f91c688fef969d53220b949488",
}
# Relay hosts expose bland names: providers scan process lists and unit names
# for proxy software. The binary is still Realm; only its installed name changes.
RELAY_BINARY_PATH = "/usr/local/bin/netfwd"
LEGACY_REALM_BINARY_PATH = "/usr/local/bin/realm"

# Xray client binary (for connection verification)
XRAY_VERSION = "26.7.28"  # matches xray bundled in remnawave/node:3.4.2
XRAY_GITHUB_URL = "https://github.com/XTLS/Xray-core/releases/download"
XRAY_ASSET_MAP: dict[tuple[str, str], str] = {
    ("Darwin", "arm64"): "Xray-macos-arm64-v8a.zip",
    ("Darwin", "x86_64"): "Xray-macos-64.zip",
    ("Linux", "x86_64"): "Xray-linux-64.zip",
    ("Linux", "aarch64"): "Xray-linux-arm64-v8a.zip",
}
# sha256 of each XRAY_VERSION archive as GitHub publishes it (the same as the release's .dgst SHA2-256)
XRAY_SHA256: dict[str, str] = {
    "Xray-macos-arm64-v8a.zip": "9b99a351febe31b7e0c7f22deeb1577a1da0b98aaa51aec7fd17832e68cf63d6",
    "Xray-macos-64.zip": "812f7d9de6d3506795eabda2f6928ba301c632c3fe6fa39c52ea8e0ed9e4e244",
    "Xray-linux-64.zip": "8195d909f1109b8f3d99eefe401a3c451d7bf4af71f24d3815420f77e5dd2a40",
    "Xray-linux-arm64-v8a.zip": "f5698bb218ada3b4022db26fafc39601c5f53b46b19eb76c9616325985807501",
}

# Xray archive sizes, which the node agent checks before the hash (`binary_pin.size`)
XRAY_ARCHIVE_BYTES: dict[str, int] = {
    "Xray-linux-64.zip": 21164807,
    "Xray-linux-arm64-v8a.zip": 19699639,
}

# uv installs Meridian's control service (`meridian serve`) with its own Python on the control
# server; the static musl build runs on every supported distribution.
UV_VERSION = "0.13.6"
UV_GITHUB_URL = "https://github.com/astral-sh/uv/releases/download"
UV_SHA256: dict[str, str] = {
    "x86_64": "5d61f26b6c7213779eda90edc6971ea6b0b796bfb8d49a574c8ad69b59838548",
    "aarch64": "819692e404d23e5aae6bc44c0e260f1f5492fd1910ab4693b2c650f926dbe800",
}
CONTROL_SERVICE_PYTHON = "3.12"

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
