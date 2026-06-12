"""Server resolution logic shared across commands."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from meridian.config import SERVER_CREDS_DIR, creds_dir_for, is_ip
from meridian.console import err_console, fail, info, warn
from meridian.servers import SERVER_ROLE_RELAY, ServerEntry, ServerRegistry
from meridian.ssh import ServerConnection, SSHError
from meridian.ssh_ui import RichSSHUI

if TYPE_CHECKING:
    from meridian.credentials import ServerCredentials

LOCAL_KEYWORDS = ("local", "locally")

# Servers that have already shown a version mismatch warning this session
_warned_servers: set[str] = set()

_VALID_SSH_USER = re.compile(r"^[a-zA-Z0-9._-]+$")


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


def _detect_local_mode_from_creds() -> str | None:
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


def _auto_selectable_entries(registry: ServerRegistry) -> list[ServerEntry]:
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


def resolve_server(
    registry: ServerRegistry,
    requested_server: str = "",
    explicit_ip: str = "",
    user: str = "",
    port: int = 0,
) -> ResolvedServer:
    """Resolve which server to target.

    Priority: explicit IP / 'local' keyword > --server flag > local mode (root) > single registered > fail.

    The 'local' keyword (or 'locally') triggers on-server deployment without SSH.

    If user is empty, it's auto-resolved from the server registry.
    If user is explicitly set, it overrides the registry value.
    If port is 0, it's auto-resolved from the server registry (default 22).
    If port is explicitly set (non-zero), it overrides the registry value.
    """
    ip = ""
    registry_user = ""
    registry_port = 22
    registry_key_path = ""
    local_mode = False

    # 1. Explicit IP argument or 'local' keyword takes highest priority
    if explicit_ip:
        if is_local_keyword(explicit_ip):
            detected_ip = detect_public_ip()
            if not detected_ip:
                fail(
                    "Could not detect this server's public IP",
                    hint="Provide the IP explicitly: meridian deploy 1.2.3.4",
                    hint_type="system",
                )
            ip = detected_ip
            local_mode = True
            info(f"Local mode: deploying on this server ({ip})")
        else:
            ip = explicit_ip
            # Check registry for saved user
            entry = registry.find(explicit_ip)
            if entry:
                registry_user = entry.user
                registry_port = entry.port
                registry_key_path = entry.key_path

    # 2. --server flag (resolve via registry, or 'local' keyword)
    elif requested_server:
        if is_local_keyword(requested_server):
            detected_ip = detect_public_ip()
            if not detected_ip:
                fail(
                    "Could not detect this server's public IP",
                    hint="Provide the IP explicitly: meridian <command> 1.2.3.4",
                    hint_type="system",
                )
            ip = detected_ip
            local_mode = True
            info(f"Local mode: using this server ({ip})")
        else:
            entry = registry.find(requested_server)
            if entry:
                ip = entry.host
                registry_user = entry.user
                registry_port = entry.port
                registry_key_path = entry.key_path
            elif is_ip(requested_server):
                ip = requested_server
            else:
                fail(
                    f"Server '{requested_server}' not found",
                    hint="See registered servers: meridian server list",
                    hint_type="user",
                )

    # 3. Running on the server itself as root — /etc/meridian/ readable
    else:
        local_ip = _detect_local_mode_from_creds()
        if local_ip:
            ip = local_ip
            local_mode = True

        # 4. Single server auto-select
        else:
            selectable_entries = _auto_selectable_entries(registry)
            if len(selectable_entries) == 1:
                entry = selectable_entries[0]
                ip = entry.host
                registry_user = entry.user
                registry_port = entry.port
                registry_key_path = entry.key_path
                label = f"{entry.name} ({ip})" if entry.name else ip
                info(f"Using server: {label}")

            elif len(selectable_entries) > 1:
                err_console.print("\n  Multiple servers. Use [bold]--server NAME[/bold]:\n")
                for entry in selectable_entries:
                    label = entry.name or entry.host
                    err_console.print(f"    [info]{label:<15s}[/info]  {entry.host}  ({entry.user})")
                err_console.print()
                fail(
                    "Specify a server with --server",
                    hint="Example: meridian <command> --server NAME",
                    hint_type="user",
                )

            else:
                fail("No servers configured", hint="Deploy a server first: meridian deploy IP", hint_type="user")

    # Resolve user: explicit flag > registry > default root
    resolved_user = user or registry_user or "root"

    if not _VALID_SSH_USER.match(resolved_user):
        fail(
            f"SSH user '{resolved_user}' is invalid",
            hint="Use letters, numbers, dots, hyphens, and underscores.",
            hint_type="user",
        )

    # Resolve port: explicit flag > registry > default 22
    resolved_port = port if port else registry_port
    resolved_key_path = registry_key_path if registry_key_path and (not user or user == registry_user) else ""

    # Determine creds_dir
    creds_dir = creds_dir_for(ip, local_mode=local_mode)

    conn = ServerConnection(
        ip=ip,
        user=resolved_user,
        local_mode=local_mode,
        port=resolved_port,
        identity_file="" if local_mode else resolved_key_path,
        multiplex=not resolved_key_path,
    )

    return ResolvedServer(
        ip=ip,
        user=resolved_user,
        local_mode=local_mode,
        creds_dir=creds_dir,
        conn=conn,
    )


def try_resolve_server(
    registry: ServerRegistry,
    requested_server: str = "",
    explicit_ip: str = "",
    user: str = "root",
) -> ResolvedServer | None:
    """Like resolve_server but returns None instead of exiting on failure."""
    try:
        return resolve_server(registry, requested_server=requested_server, explicit_ip=explicit_ip, user=user)
    except SystemExit:
        return None


def ensure_server_connection(resolved: ResolvedServer) -> ResolvedServer:
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
        resolved.conn.check_ssh(ui=RichSSHUI())
    except SSHError as exc:
        fail(str(exc), hint=exc.hint, hint_type=exc.hint_type)
    return resolved


def fetch_credentials(resolved: ResolvedServer, *, force: bool = False) -> bool:
    """Fetch credentials from server.

    When ``force`` is False, an existing local ``proxy.yml`` short-circuits.
    Write commands should pass ``force=True`` so the server remains the source
    of truth before local mutation.
    """
    proxy_file = resolved.creds_dir / "proxy.yml"
    if not force:
        try:
            if proxy_file.is_file():
                _check_version_mismatch(resolved.ip, proxy_file)
                return True
        except (PermissionError, OSError):
            pass
    try:
        resolved.creds_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    except PermissionError:
        return False
    ok = resolved.conn.fetch_credentials(resolved.creds_dir)
    if ok:
        _check_version_mismatch(resolved.ip, proxy_file)
    return ok


def _check_version_mismatch(server_ip: str, proxy_file: Path) -> None:
    """Warn once per session if the server was deployed with a different CLI version."""
    if server_ip in _warned_servers:
        return

    from meridian.credentials import ServerCredentials

    creds = ServerCredentials.load(proxy_file)
    deployed_with = creds.server.deployed_with
    if not deployed_with:
        return  # Legacy credentials — no version info

    from meridian import __version__

    try:
        from packaging.version import Version

        deployed = Version(deployed_with)
        current = Version(__version__)
    except (ImportError, ValueError):
        return  # Unparseable version or missing packaging — skip silently

    if deployed.major == current.major and deployed.minor == current.minor:
        return  # Patch differences are fine

    _warned_servers.add(server_ip)
    err_console.print()
    warn("Version mismatch")
    err_console.print(
        f"    Server deployed with Meridian [bold]{deployed_with}[/bold] — you're running [bold]{__version__}[/bold]."
    )
    err_console.print()
    err_console.print("    To update the server:")
    err_console.print(f"      [info]meridian deploy {server_ip}[/info]       Re-provisions configs (nginx, services)")
    err_console.print(f"      [info]meridian teardown {server_ip}[/info]    Full reset (then re-deploy from scratch)")
    err_console.print()
    err_console.print("    To match the server instead:")
    err_console.print(f"      [info]uv tool install meridian-vpn=={deployed_with}[/info]")
    err_console.print()
