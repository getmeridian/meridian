"""Server resolution logic for CLI commands.

Data structures and pure helpers live in ``meridian.resolve``; this module
contains only CLI-specific resolution functions that depend on Rich, Typer,
prompts, or ``console.fail()``.
"""

from __future__ import annotations

from pathlib import Path

from meridian import resolve as _resolve_lib
from meridian.config import creds_dir_for, is_ip
from meridian.console import err_console, fail, info, warn
from meridian.resolve import ResolvedServer
from meridian.resolve import (
    ensure_server_connection as _ensure_server_connection,
)
from meridian.servers import ServerRegistry
from meridian.ssh import ServerConnection
from meridian.ssh_ui import RichSSHUI

__all__ = [
    "ensure_server_connection",
    "fetch_credentials",
    "resolve_server",
    "try_resolve_server",
]

# Servers that have already shown a version mismatch warning this session
_warned_servers: set[str] = set()


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
        if _resolve_lib.is_local_keyword(explicit_ip):
            detected_ip = _resolve_lib.detect_public_ip()
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
        if _resolve_lib.is_local_keyword(requested_server):
            detected_ip = _resolve_lib.detect_public_ip()
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
        local_ip = _resolve_lib.detect_local_mode_from_creds()
        if local_ip:
            ip = local_ip
            local_mode = True

        # 4. Single server auto-select
        else:
            selectable_entries = _resolve_lib.auto_selectable_entries(registry)
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

    if not _resolve_lib.VALID_SSH_USER.match(resolved_user):
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
    """CLI wrapper — delegates to ``meridian.resolve`` with Rich SSH UI."""
    return _ensure_server_connection(resolved, ui=RichSSHUI())


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
