"""Server management — add, list, remove known servers."""

from __future__ import annotations

import shutil

from meridian.commands._validation import validate_command_input
from meridian.config import CREDS_BASE, SERVERS_FILE, sanitize_ip_for_path
from meridian.console import err_console, fail, info, line, ok, warn
from meridian.core.command_inputs import ServerAddRequest, ServerRemoveRequest
from meridian.servers import ServerEntry, ServerRegistry
from meridian.ssh import ServerConnection, SSHError


def run_add(ip: str, name: str = "", user: str = "root") -> None:
    """Register a server, verify SSH, and fetch credentials."""
    request = validate_command_input(ServerAddRequest, "Invalid server add request", ip=ip, name=name, user=user)

    registry = ServerRegistry(SERVERS_FILE)
    conn = ServerConnection(ip=request.ip, user=request.user, local_mode=False)

    info(f"Connecting to {request.ip}...")
    try:
        conn.check_ssh()
    except SSHError as exc:
        fail(str(exc), hint=exc.hint, hint_type=exc.hint_type)

    # Fetch credentials from server
    creds_dir = CREDS_BASE / sanitize_ip_for_path(request.ip)
    creds_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if conn.fetch_credentials(creds_dir):
        ok("Fetched credentials from server")
    else:
        warn("No credentials found on server (run meridian deploy first)")

    registry.add(ServerEntry(host=request.ip, user=request.user, name=request.name))
    ok(f"Server added: {request.name or request.ip}")


def run_list() -> None:
    """Display all registered servers."""
    registry = ServerRegistry(SERVERS_FILE)
    entries = registry.list()

    if not entries:
        info("No servers configured. Run: meridian deploy IP")
        return

    err_console.print()
    err_console.print(f"  [bold]{'NAME':<15s}  {'IP':<39s}  {'USER':<8s}[/bold]")
    line()
    for entry in entries:
        label = entry.name if entry.name else "--"
        err_console.print(f"  {label:<15s}  {entry.host:<39s}  {entry.user:<8s}")
    err_console.print()


def run_remove(query: str) -> None:
    """Remove a server by IP or name, including local credentials."""
    request = validate_command_input(ServerRemoveRequest, "Invalid server remove request", query=query)
    registry = ServerRegistry(SERVERS_FILE)

    entry = registry.find(request.query)
    if not entry:
        fail(f"Server '{request.query}' not found", hint_type="user")

    host = entry.host
    registry.remove(request.query)

    # Remove local credentials
    creds_dir = CREDS_BASE / sanitize_ip_for_path(host)
    if creds_dir.exists():
        shutil.rmtree(creds_dir)

    ok(f"Server removed: {request.query}")
