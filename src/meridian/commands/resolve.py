"""Server resolution logic for CLI commands.

Data structures and pure helpers live in ``meridian.resolve``; this module
contains only CLI-specific resolution functions that depend on Rich, Typer,
prompts, or ``console.fail()``.
"""

from __future__ import annotations

import typer
from rich.markup import escape

from meridian import resolve as _resolve_lib
from meridian.config import is_ip
from meridian.console import err_console, fail, info
from meridian.core.errors import LocalStateError
from meridian.resolve import ResolvedServer
from meridian.resolve import (
    ensure_server_connection as _ensure_server_connection,
)
from meridian.servers import ServerRegistry
from meridian.ssh import ServerConnection, SSHError
from meridian.ssh_ui import RichSSHUI

__all__ = [
    "ensure_server_connection",
    "resolve_server",
    "try_resolve_server",
]


def _resolve_server(
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
                    hint="Provide the IP explicitly: meridian deploy 198.51.100.10",
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
                    hint="Provide the IP explicitly: meridian <command> 198.51.100.10",
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
        local_ip = _resolve_lib.detect_local_server_ip()
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
                    err_console.print(
                        f"    [info]{escape(label):<15s}[/info]  {escape(entry.host)}  ({escape(entry.user)})"
                    )
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
        conn=conn,
    )


def resolve_server(
    registry: ServerRegistry,
    requested_server: str = "",
    explicit_ip: str = "",
    user: str = "",
    port: int = 0,
) -> ResolvedServer:
    """Resolve a target and render unsafe local-state failures consistently."""
    try:
        return _resolve_server(
            registry,
            requested_server=requested_server,
            explicit_ip=explicit_ip,
            user=user,
            port=port,
        )
    except LocalStateError as exc:
        fail(exc)


def try_resolve_server(
    registry: ServerRegistry,
    requested_server: str = "",
    explicit_ip: str = "",
    user: str = "root",
) -> ResolvedServer | None:
    """Like resolve_server but returns None instead of exiting on failure."""
    try:
        return _resolve_server(registry, requested_server=requested_server, explicit_ip=explicit_ip, user=user)
    except LocalStateError as exc:
        fail(exc)
    except (SystemExit, typer.Exit):
        return None


def ensure_server_connection(resolved: ResolvedServer) -> ResolvedServer:
    """CLI wrapper — delegates to ``meridian.resolve`` with Rich SSH UI.

    Catches ``SSHError`` from the library layer and converts it to a
    ``fail()`` exit with the appropriate hint.
    """

    try:
        return _ensure_server_connection(resolved, ui=RichSSHUI())
    except SSHError as exc:
        fail(str(exc), hint=exc.hint, hint_type=exc.category)
