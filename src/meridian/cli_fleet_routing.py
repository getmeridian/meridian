"""CLI arguments for the reviewed client routing choice."""

from __future__ import annotations

import typer

from meridian.cli_options import Json, Yes


def fleet_routing_cmd(
    policy: str | None = typer.Argument(
        None, metavar="POLICY", help="ru-split or full; omit to read the current policy"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Review the policy change without applying it"),
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Show or change client routing: ru-split or full

    Both policies bypass private/LAN ranges and retain Xray's best-effort
    BitTorrent bypass. A change is reviewed and confirmed before apply.
    Apps receive it on their next subscription refresh.

    [dim]Examples:[/dim]
      [cyan]meridian fleet routing[/cyan]
      [cyan]meridian fleet routing full --dry-run --json[/cyan]
      [cyan]meridian fleet routing full --yes[/cyan]
    """
    from meridian.commands.fleet_routing import run_routing

    run_routing(policy, dry_run=dry_run, yes=yes)
