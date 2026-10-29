"""`meridian panel` — open the fleet's web console yourself.

Prints the control service's console URL and, until the console has an owner
(or with `--setup-link`), a one-time setup link.
"""

from __future__ import annotations

from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.console import err_console, fail
from meridian.control.client import ControlClient, ControlServiceError, ControlServiceNotFound
from meridian.core.errors import LocalStateError, RemnawaveFleetError


def run(*, setup_link: bool = False) -> None:
    """Print the web console's URL; a setup link only on request or first use."""
    try:
        cluster = ClusterConfig.load()
    except (LocalStateError, OSError, ValueError) as exc:
        fail(f"Cannot read the fleet configuration: {exc}", hint_type="user")
    panel = cluster.panel
    if not panel.url:
        fail("No panel is deployed yet.", hint="Deploy one first: meridian deploy SERVER", hint_type="user")
    if not cluster.runs_control_service:
        fail(RemnawaveFleetError())
    _console(cluster, setup_link=setup_link)


def _console(cluster: ClusterConfig, *, setup_link: bool) -> None:
    """The control service's web console: its URL, and a one-time setup link when one is due."""
    url = cluster.panel.display_url
    try:
        # Minting a link changes nothing a snapshot keeps (`ui_*` tables never ride one), so no backup pull follows.
        with ControlClient(cluster.panel.url, cluster.panel.api_token, report_mutations=False) as client:
            owner = client.owner_status()
            # Only --setup-link asks for a reset: bare `panel` on a fresh service gets a setup link, never a reset.
            grant = client.issue_setup_link(reset=setup_link) if setup_link or not owner.exists else None
    except ControlServiceNotFound:
        fail(
            "This control service predates the web console.",
            hint="Update it from here, then run meridian panel again: meridian apply",
            hint_type="user",
        )
    except ControlServiceError as exc:
        fail(exc)

    err_console.print()
    err_console.print("  [bold]Meridian console[/bold]")
    err_console.print(f"  URL         [bold]{escape(url)}[/bold]")
    if grant is not None:
        err_console.print(f"  Setup link  [bold]{escape(url)}#setup={escape(grant.token)}[/bold]")
        if grant.purpose == "reset":
            err_console.print(
                "              [dim]Works once, for 15 minutes. It sets a new password, turns the sign-in[/dim]\n"
                "              [dim]code off, and signs out every device.[/dim]"
            )
        else:
            err_console.print(
                "              [dim]Works once, for 15 minutes. Open it to choose the owner's password.[/dim]"
            )
    else:
        code = "with a sign-in code" if owner.totp_enabled else "password only"
        err_console.print(f"  Owner       set up ({code})")
        err_console.print("  [dim]Lost the password? A fresh setup link: meridian panel --setup-link[/dim]")
    err_console.print()
    err_console.print("  [dim]Treat the URL and any setup link like passwords; neither is ever saved in a log.[/dim]")
    err_console.print("  [bold]The console changes[/bold] people, API tokens, and the fleet title.")
    err_console.print("  [bold]Servers change here[/bold], with meridian: the control server never holds SSH keys.")
    err_console.print()
