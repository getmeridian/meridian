"""Server resolution primitives — shared across commands and library code.

CLI-specific resolution logic (interactive prompts, Rich output) stays in
``meridian.commands.resolve``; this module holds the data structures and
pure helpers that library code may import without pulling in the CLI layer.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from meridian.config import is_ip
from meridian.servers import ServerEntry, ServerRegistry
from meridian.ssh import SSHUI, ServerConnection

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
    conn: ServerConnection


def detect_local_server_ip() -> str | None:
    """Check if we're running on a deployed server and extract its IP.

    Requires the v4 server identity marker and a panel node in cluster.yml.
    """
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
    return None


def auto_selectable_entries(registry: ServerRegistry) -> list[ServerEntry]:
    """Return the best registry subset for implicit server selection.

    Relay-only hosts are excluded using cluster.yml. A server that is both an
    exit node and a relay remains selectable.
    """
    entries = registry.list()
    try:
        from meridian.cluster import ClusterConfig

        cluster = ClusterConfig.load()
        node_hosts = {node.ip for node in cluster.nodes if node.ip}
        relay_only_hosts = {relay.ip for relay in cluster.relays if relay.ip} - node_hosts
    except (OSError, ValueError):
        relay_only_hosts = set()

    exit_entries = [entry for entry in entries if entry.host not in relay_only_hosts]
    if exit_entries:
        return exit_entries
    if relay_only_hosts:
        return []
    return entries


def ensure_server_connection(
    resolved: ResolvedServer,
    *,
    ui: SSHUI | None = None,
) -> ResolvedServer:
    """Detect local mode if not already set, then verify SSH connectivity.

    Local mode activates for root (who can read /etc/meridian/) and for
    non-root users (who use sudo for commands).

    Returns a new ResolvedServer with updated local_mode if changed.
    """
    if not resolved.local_mode:
        if resolved.conn.detect_local_mode():
            resolved = ResolvedServer(
                ip=resolved.ip,
                user=resolved.user,
                local_mode=True,
                conn=resolved.conn,
            )
    resolved.conn.check_ssh(ui=ui)
    return resolved
