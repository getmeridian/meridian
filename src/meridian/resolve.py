"""Server resolution primitives — shared across commands and library code.

CLI-specific resolution logic (interactive prompts, Rich output) stays in
``meridian.commands.resolve``; this module holds the data structures and
pure helpers that library code may import without pulling in the CLI layer.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from meridian.config import SERVER_CREDS_DIR, creds_dir_for, is_ip
from meridian.console import fail
from meridian.servers import SERVER_ROLE_RELAY, ServerEntry, ServerRegistry
from meridian.ssh import SSHUI, ServerConnection, SSHError

if TYPE_CHECKING:
    from meridian.credentials import ServerCredentials

LOCAL_KEYWORDS = ("local", "locally")

VALID_SSH_USER = re.compile(r"^[a-zA-Z0-9._-]+$")


def is_local_keyword(value: str) -> bool:
    """Check if a value is the 'local' keyword for on-server deployment."""
    return value.lower() in LOCAL_KEYWORDS


def detect_public_ip() -> str:
    """Detect the machine's public IP address (prefers IPv4)."""
    # Try IPv4 first (most common, backward compatible)
    for url in ("https://ifconfig.me", "https://api.ipify.org"):
        try:
            result = subprocess.run(
                ["curl", "-4", "-s", "--max-time", "3", url],
                capture_output=True,
                text=True,
                timeout=5,
                stdin=subprocess.DEVNULL,
            )
            ip = result.stdout.strip()
            if is_ip(ip):
                return ip
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    # Fall back to IPv6
    for url in ("https://ifconfig.me", "https://api64.ipify.org"):
        try:
            result = subprocess.run(
                ["curl", "-6", "-s", "--max-time", "3", url],
                capture_output=True,
                text=True,
                timeout=5,
                stdin=subprocess.DEVNULL,
            )
            ip = result.stdout.strip()
            if is_ip(ip):
                return ip
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return ""


@dataclass(frozen=True)
class ResolvedServer:
    """Result of server resolution — everything needed to interact with a server."""

    ip: str
    user: str
    local_mode: bool
    creds_dir: Path
    conn: ServerConnection

    @property
    def creds(self) -> ServerCredentials:
        """Load credentials from the resolved creds_dir."""
        from meridian.credentials import ServerCredentials

        return ServerCredentials.load(self.creds_dir / "proxy.yml")


def detect_local_mode_from_creds() -> str | None:
    """Check if we're running on a deployed server and extract its IP.

    Tries v4 cluster.yml first (node with is_panel_host), then falls back
    to v3 /etc/meridian/proxy.yml. Only succeeds for root.
    """
    # v4: check cluster.yml for a panel node matching this server
    try:
        from meridian.cluster import ClusterConfig
        from meridian.config import SERVER_NODE_CONFIG

        if SERVER_NODE_CONFIG.is_file():
            cluster = ClusterConfig.load()
            panel_node = cluster.panel_node
            if panel_node and panel_node.ip:
                return panel_node.ip
    except (PermissionError, OSError):
        pass

    # v3 compat: /etc/meridian/proxy.yml
    proxy = SERVER_CREDS_DIR / "proxy.yml"
    try:
        if not proxy.is_file():
            return None
        from meridian.credentials import ServerCredentials

        creds = ServerCredentials.load(proxy)
        return creds.server.ip or None
    except (PermissionError, OSError):
        return None


def _find_proxy_file(host: str) -> Path | None:
    """Find locally cached credentials for a host, if present."""
    from meridian import config as cfg

    remote = cfg.CREDS_BASE / cfg.sanitize_ip_for_path(host) / "proxy.yml"
    if remote.is_file():
        return remote

    local = cfg.SERVER_CREDS_DIR / "proxy.yml"
    try:
        if local.is_file():
            from meridian.credentials import ServerCredentials

            creds = ServerCredentials.load(local)
            if creds.server.ip == host:
                return local
    except (PermissionError, OSError):
        return None

    return None


def _find_relay_file(host: str) -> Path | None:
    """Find locally cached relay metadata for a host, if present."""
    from meridian import config as cfg

    relay = cfg.CREDS_BASE / cfg.sanitize_ip_for_path(host) / "relay.yml"
    if relay.is_file():
        return relay
    return None


def _cached_relay_hosts(entries: list[ServerEntry]) -> set[str]:
    """Infer legacy relay entries from locally cached exit credentials."""
    from meridian.credentials import ServerCredentials

    relay_hosts: set[str] = set()
    for entry in entries:
        proxy_file = _find_proxy_file(entry.host)
        if proxy_file is None:
            continue
        try:
            creds = ServerCredentials.load(proxy_file)
        except (PermissionError, OSError):
            continue
        relay_hosts.update(relay.ip for relay in creds.relays if relay.ip)
    return relay_hosts


def _is_relay_entry(entry: ServerEntry, cached_relay_hosts: set[str]) -> bool:
    """Determine whether a registry entry is a relay rather than an exit."""
    if entry.role == SERVER_ROLE_RELAY:
        return True
    if _find_relay_file(entry.host) is not None:
        return True
    return entry.host in cached_relay_hosts


def auto_selectable_entries(registry: ServerRegistry) -> list[ServerEntry]:
    """Return the best registry subset for implicit server selection.

    Relay nodes share the registry with exit servers. New relay entries are
    tagged explicitly; older ones are inferred from local relay metadata or
    from cached exit credentials that mention them.
    """
    entries = registry.list()
    cached_relay_hosts = _cached_relay_hosts(entries)
    exit_entries = [entry for entry in entries if not _is_relay_entry(entry, cached_relay_hosts)]
    if exit_entries:
        return exit_entries
    if cached_relay_hosts or any(entry.role == SERVER_ROLE_RELAY or _find_relay_file(entry.host) for entry in entries):
        return []
    return entries


def ensure_server_connection(
    resolved: ResolvedServer,
    *,
    ui: SSHUI | None = None,
) -> ResolvedServer:
    """Detect local mode if not already set, then verify SSH connectivity.

    Local mode activates for root (who can read /etc/meridian/) and for
    non-root users (who use sudo for commands). Non-root users keep
    creds_dir in their home directory (sudo copies from /etc/meridian/).

    Returns a new ResolvedServer with updated local_mode/creds_dir if changed.
    """
    if not resolved.local_mode:
        if resolved.conn.detect_local_mode():
            resolved = ResolvedServer(
                ip=resolved.ip,
                user=resolved.user,
                local_mode=True,
                creds_dir=creds_dir_for(resolved.ip, local_mode=True),
                conn=resolved.conn,
            )
    try:
        resolved.conn.check_ssh(ui=ui)
    except SSHError as exc:
        fail(str(exc), hint=exc.hint, hint_type=exc.hint_type)
    return resolved
