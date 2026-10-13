"""`meridian panel` — open the Remnawave admin panel yourself, with the ownership contract spelled out."""

from __future__ import annotations

from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.console import err_console, fail
from meridian.core.errors import LocalStateError

_MERIDIAN_OWNS = (
    "Nodes, config profiles and inbounds, published Hosts, internal and external squads",
    "Subscription templates and the settings Meridian sets (other Response Rules are kept)",
    "Each declared client's existence, name, and squads",
)
_YOURS = (
    "Client status (enable/disable), traffic limits, expiry, descriptions, HWID limits",
    "Users you create in the panel (Meridian never lists, changes, or deletes them)",
    "Panel settings Meridian does not set: notifications, plugins, IP control, admin account",
)


def run(*, show_password: bool = False) -> None:
    """Print the panel URL and admin login; the password only on request."""
    try:
        cluster = ClusterConfig.load()
    except (LocalStateError, OSError, ValueError) as exc:
        fail(f"Cannot read the fleet configuration: {exc}", hint_type="user")
    panel = cluster.panel
    if not panel.url:
        fail("No panel is deployed yet.", hint="Deploy one first: meridian deploy SERVER", hint_type="user")

    err_console.print()
    err_console.print("  [bold]Remnawave panel[/bold]")
    err_console.print(f"  URL       [bold]{escape(panel.display_url)}[/bold]")
    if panel.admin_user:
        err_console.print(f"  Username  {escape(panel.admin_user)}")
        if show_password:
            err_console.print(f"  Password  {escape(panel.admin_pass)}")
        else:
            err_console.print("  Password  [dim]hidden — add --show-password to print it[/dim]")
    else:
        err_console.print("  [warn]No saved admin login; this panel was set up outside Meridian.[/warn]")
    err_console.print()
    err_console.print("  [dim]Treat the URL like a password: anyone who has it can try to log in.[/dim]")
    err_console.print(
        "  [warn]Don't change this admin's password or delete the meridian-provisioner API token:[/warn]\n"
        "  [warn]Meridian uses them to manage the panel. Create your own API token if a script needs one.[/warn]"
    )
    err_console.print()
    err_console.print("  [bold]Meridian manages[/bold] [dim](edits here are reverted by the next apply)[/dim]")
    for line in _MERIDIAN_OWNS:
        err_console.print(f"    - {line}")
    err_console.print("  [bold]Yours to change[/bold] [dim](apply never touches these)[/dim]")
    for line in _YOURS:
        err_console.print(f"    - {line}")
    err_console.print()
    err_console.print("  [dim]See pending differences any time: meridian plan[/dim]")
    err_console.print()
