"""Rich rendering helpers for relay deployment."""

from __future__ import annotations

from rich.panel import Panel

from meridian.config import REALM_VERSION
from meridian.console import err_console, line, ok
from meridian.core.command_inputs import RelayDeployRequest


def render_relay_deployment_plan(
    request: RelayDeployRequest,
    *,
    exit_ip: str,
    relay_sni: str,
) -> None:
    """Render the reviewed legacy relay deployment topology."""
    summary = (
        f"Relay:  {request.user}@{request.relay_ip}:{request.listen_port}  |  Exit: {exit_ip}:443\n"
        f"Engine: Realm v{REALM_VERSION}  |  Name: {request.relay_name or '(auto)'}  |  SNI: {relay_sni}\n\n"
        f"  Client -> {request.relay_ip}:{request.listen_port} -> {exit_ip}:443 -> Internet\n"
        "  Encryption: end-to-end (relay cannot read content)"
    )
    err_console.print()
    err_console.print(Panel(summary, title="[bold]Relay deployment plan[/bold]", border_style="cyan", padding=(0, 2)))
    err_console.print()


def render_relay_deployment_success(request: RelayDeployRequest, *, exit_ip: str) -> None:
    """Render legacy relay success and follow-up commands."""
    err_console.print()
    ok(f"Relay {request.relay_ip} forwarding to exit {exit_ip}")
    route = f"Client -> {request.relay_ip}:{request.listen_port} (domestic) -> {exit_ip}:443 (abroad) -> Internet"
    err_console.print(f"  [dim]{route}[/dim]")
    err_console.print("  [dim]Subscriptions auto-update -- clients get relay URLs on next sync.[/dim]")
    err_console.print()
    err_console.print("  [bold]Next steps:[/bold]")
    err_console.print("    meridian client add alice          [dim]# relay URLs included[/dim]")
    err_console.print(f"    meridian relay check {request.relay_ip}    [dim]# verify relay health[/dim]")
    err_console.print("    meridian relay list                [dim]# list all relays[/dim]")
    err_console.print()
    line()
