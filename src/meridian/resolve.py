"""Server resolution primitives — shared across commands and library code.

CLI-specific resolution logic (interactive prompts, Rich output) stays in
``meridian.commands.resolve``; this module holds the data structures and
pure helpers that library code may import without pulling in the CLI layer.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

from meridian.config import is_ip
from meridian.servers import ServerEntry, ServerRegistry, this_machine_id
from meridian.ssh import SSHUI, ServerConnection

LOCAL_KEYWORDS = ("local", "locally")

VALID_SSH_USER = re.compile(r"^[a-zA-Z0-9._-]+$")


@dataclass(frozen=True)
class TargetSpec:
    """A parsed ``[user@]host[:port]`` / saved-name / ``local`` command target.

    ``host`` is still unresolved: it may be an IP, a hostname, or a saved
    server name. Empty ``user`` and zero ``port`` mean "saved value, else default".
    """

    host: str = ""
    user: str = ""
    port: int = 0
    local: bool = False


_PORT_SUFFIX = re.compile(r"^(?P<host>[^:\[\]]+):(?P<port>\d+)$")
_BRACKETED = re.compile(r"^\[(?P<host>[0-9A-Fa-f:.]+)\](?::(?P<port>\d+))?$")


def parse_target(text: str) -> TargetSpec:
    """Parse one positional target like ssh does: ``user@host:port``, ``[v6]:port``, a name, or ``local``.

    Raises ``ValueError`` with an operator-facing message for malformed input.
    """
    value = text.strip()
    if not value:
        return TargetSpec()
    if is_local_keyword(value):
        return TargetSpec(local=True)
    user = ""
    if "@" in value:
        user, _, value = value.rpartition("@")
        if not VALID_SSH_USER.match(user):
            raise ValueError(f"SSH user '{user}' is invalid; use letters, numbers, dots, hyphens, and underscores.")
    port_text = ""
    if bracketed := _BRACKETED.match(value):
        value, port_text = bracketed["host"], bracketed["port"] or ""
    elif suffixed := _PORT_SUFFIX.match(value):
        value, port_text = suffixed["host"], suffixed["port"]
    if not value:
        raise ValueError(f"'{text}' has no host.")
    port = int(port_text) if port_text else 0
    if port_text and not 1 <= port <= 65535:
        raise ValueError(f"SSH port {port_text} is out of range (1-65535).")
    return TargetSpec(host=value, user=user, port=port)


def merge_target(text: str, *, user: str = "", port: int = 0) -> TargetSpec:
    """Parse *text* and fold in ``--user``/``--ssh-port`` flags, refusing contradictions."""
    target = parse_target(text)
    for label, flag, inline in (("SSH user", user, target.user), ("SSH port", port, target.port)):
        if flag and inline and flag != inline:
            raise ValueError(f"{label} given twice: {inline} in the target and {flag} as a flag.")
    return TargetSpec(host=target.host, user=user or target.user, port=port or target.port, local=target.local)


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

    @property
    def port(self) -> int:
        return self.conn.port

    @property
    def machine_id(self) -> str:
        """The ID to save with the profile, so later commands on this machine skip SSH too."""
        return this_machine_id() if self.local_mode else ""


def current_login() -> str:
    """Name of the account running Meridian ('' where POSIX accounts do not exist)."""
    try:
        import pwd

        return pwd.getpwuid(os.geteuid()).pw_name
    except (ImportError, KeyError, AttributeError):
        return ""


def local_entry(registry: ServerRegistry) -> ServerEntry | None:
    """Return the saved server Meridian is running on, if exactly one matches this machine."""
    matches = [entry for entry in registry.list() if entry.runs_here()]
    return matches[0] if len(matches) == 1 else None


def connection_for_entry(entry: ServerEntry, *, user: str = "", port: int = 0) -> ServerConnection:
    """Open a saved server: locally when it is this machine, otherwise over SSH."""
    if entry.runs_here():
        return ServerConnection(entry.host, user or entry.user, local_mode=True, port=port or entry.port)
    return ServerConnection(
        entry.host,
        user or entry.user,
        port=port or entry.port,
        identity_file=entry.key_path if not user or user == entry.user else "",
    )


def auto_selectable_entries(registry: ServerRegistry) -> list[ServerEntry]:
    """Return the best registry subset for implicit server selection.

    Relay-only servers are excluded using the saved topology. A server that
    is both an exit and a relay hop remains selectable.
    """
    entries = registry.list()
    relay_only_refs: set[str] = set()
    try:
        from meridian.cluster import ClusterConfig

        intent = ClusterConfig.load().topology_intent
    except (OSError, ValueError):
        intent = None
    if intent is not None:
        relay_refs = {ref for relay in intent.transparent_relays for ref in relay.hop_server_refs}
        role_refs = {
            intent.control.server_ref,
            *(exit_.server_ref for exit_ in intent.exits),
            *(gateway.server_ref for gateway in intent.routing_gateways),
        }
        relay_only_refs = relay_refs - role_refs

    exit_entries = [entry for entry in entries if entry.id not in relay_only_refs]
    if exit_entries:
        return exit_entries
    if relay_only_refs & {entry.id for entry in entries}:
        return []
    return entries


def ensure_server_connection(
    resolved: ResolvedServer,
    *,
    ui: SSHUI | None = None,
) -> ResolvedServer:
    """Verify the resolved connection (SSH reachability, or passwordless sudo locally)."""
    resolved.conn.check_ssh(ui=ui)
    return resolved
