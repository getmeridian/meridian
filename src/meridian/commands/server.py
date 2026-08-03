"""Server management — add, list, remove known servers."""

from __future__ import annotations

from datetime import UTC, datetime

import typer
from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.commands._validation import validate_command_input
from meridian.config import SERVER_PROFILES_FILE
from meridian.console import confirm, err_console, fail, info, line, ok
from meridian.core.command_inputs import ServerAddRequest, ServerRemoveRequest
from meridian.core.errors import LocalStateError
from meridian.servers import ServerEntry, ServerRegistry
from meridian.ssh import ServerConnection, SSHError
from meridian.ssh_ui import RichSSHUI


def run_add(ip: str, name: str = "", user: str = "root", ssh_port: int = 22) -> None:
    """Register a server after verifying SSH access."""
    request = validate_command_input(
        ServerAddRequest,
        "Invalid server add request",
        ip=ip,
        name=name,
        user=user,
        ssh_port=ssh_port,
    )

    registry = ServerRegistry(SERVER_PROFILES_FILE)
    pending_entry = ServerEntry(
        host=request.ip,
        user=request.user,
        name=request.name,
        port=request.ssh_port,
        auth_state="validated",
    )
    try:
        registry.assert_can_add(pending_entry)
    except LocalStateError as exc:
        fail(exc)
    conn = ServerConnection(ip=request.ip, user=request.user, local_mode=False, port=request.ssh_port)

    info(f"Connecting to {request.ip}...")
    try:
        conn.check_ssh(ui=RichSSHUI())
    except SSHError as exc:
        fail(str(exc), hint=exc.hint, hint_type=exc.category)

    pending_entry.last_validated_at = datetime.now(UTC).isoformat()
    try:
        registry.add(pending_entry)
    except LocalStateError as exc:
        fail(exc)
    except OSError as exc:
        fail(
            f"Could not save server profile: {exc}",
            hint="Check ~/.meridian permissions and disk space, then retry.",
            hint_type="system",
        )
    ok(f"Server added: {request.name or request.ip}")


def run_list() -> None:
    """Display all registered servers."""
    registry = ServerRegistry(SERVER_PROFILES_FILE)
    try:
        entries = registry.list()
    except LocalStateError as exc:
        fail(exc)

    if not entries:
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
            hint="Check ~/.meridian permissions and disk space, then retry.",
            hint_type="system",
        )

    ok(f"Server removed: {request.query}")
