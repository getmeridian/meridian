"""Server management — add, list, remove known servers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import typer
from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.commands._helpers import fail_meridian_v3_state, has_meridian_v3_state
from meridian.commands._validation import validate_command_input
from meridian.config import HOME_DISPLAY, SERVER_PROFILES_FILE
from meridian.console import confirm, err_console, fail, info, line, ok
from meridian.core.command_inputs import ServerAddRequest, ServerRemoveRequest
from meridian.core.errors import LocalStateError
from meridian.servers import ServerEntry, ServerRegistry

if TYPE_CHECKING:
    from meridian.resolve import ResolvedServer


def run_add(ip: str, name: str = "", user: str = "", ssh_port: int = 0) -> None:
    """Register a server after verifying access: over SSH, or locally for `local`/this machine."""
    from meridian.commands.resolve import resolve_server

    registry = ServerRegistry(SERVER_PROFILES_FILE)
    resolved = resolve_server(registry, explicit_ip=ip, user=user, port=ssh_port)
    request = validate_command_input(
        ServerAddRequest,
        "Invalid server add request",
        ip=resolved.ip,
        name=name,
        user=resolved.user,
        ssh_port=resolved.port,
    )
    register_server(registry, resolved, name=request.name)
    ok(f"Server added: {request.name or request.ip}")


def register_server(registry: ServerRegistry, resolved: ResolvedServer, *, name: str = "") -> ServerEntry:
    """Verify access to a resolved target, then save (or refresh) its profile; return the saved entry.

    A saved server keeps its title unless a new name is given.
    """
    from meridian.commands.resolve import ensure_server_connection

    try:
        saved = registry.find(resolved.ip)
    except LocalStateError as exc:
        fail(exc)
    pending_entry = ServerEntry(
        host=resolved.ip,
        user=resolved.user,
        name=name or (saved.name if saved is not None else ""),
        port=resolved.port,
        auth_state="validated",
        machine_id=resolved.machine_id,
    )
    try:
        registry.assert_can_add(pending_entry)
    except LocalStateError as exc:
        fail(exc)

    if not resolved.local_mode:
        info(f"Connecting to {resolved.ip}...")
    ensure_server_connection(resolved)

    pending_entry.last_validated_at = datetime.now(UTC).isoformat()
    try:
        registry.add(pending_entry)
        entry = registry.find(resolved.ip)
    except LocalStateError as exc:
        fail(exc)
    except OSError as exc:
        fail(
            f"Could not save server profile: {exc}",
            hint=f"Check {HOME_DISPLAY} permissions and disk space, then retry.",
            hint_type="system",
        )
    if entry is None:
        raise RuntimeError("Validated server was not saved")
    return entry


def run_list() -> None:
    """Display all registered servers."""
    registry = ServerRegistry(SERVER_PROFILES_FILE)
    try:
        entries = registry.list()
    except LocalStateError as exc:
        fail(exc)

    if not entries:
        if has_meridian_v3_state():
            fail_meridian_v3_state()
        info("No servers configured. Run: meridian deploy IP")
        return

    err_console.print()
    err_console.print(f"  [bold]{'NAME':<15s}  {'IP':<39s}  {'USER':<8s}  {'PORT':<5s}[/bold]")
    line()
    for entry in entries:
        label = escape(entry.name) if entry.name else "--"
        err_console.print(f"  {label:<15s}  {escape(entry.host):<39s}  {escape(entry.user):<8s}  {entry.port:<5d}")
    err_console.print()


def run_remove(query: str, *, yes: bool = False) -> None:
    """Remove a server by IP or name."""
    request = validate_command_input(ServerRemoveRequest, "Invalid server remove request", query=query)
    registry = ServerRegistry(SERVER_PROFILES_FILE)

    try:
        entry = registry.find(request.query)
    except LocalStateError as exc:
        fail(exc)
    if not entry:
        fail(f"Server '{request.query}' not found", hint_type="user")

    if not yes and not confirm(f"Remove saved server '{escape(entry.name or entry.host)}'?"):
        raise typer.Exit(1)

    try:
        registry.remove(request.query, cluster=ClusterConfig.load())
    except LocalStateError as exc:
        fail(exc)
    except OSError as exc:
        fail(
            f"Could not update saved servers: {exc}",
            hint=f"Check {HOME_DISPLAY} permissions and disk space, then retry.",
            hint_type="system",
        )

    ok(f"Server removed: {request.query}")
