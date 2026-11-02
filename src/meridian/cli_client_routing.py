"""Private person policy selection; the service owns the review and write."""

from __future__ import annotations

import typer

from meridian.cli_options import Json, Yes


def client_routing_cmd(
    person: str = typer.Argument(..., help="Person's username or display name"),
    policy: str | None = typer.Argument(None, help="Reviewed policy name; omit to read the current choice"),
    inherit: bool = typer.Option(False, "--inherit", help="Clear the override and follow profile/fleet defaults"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview the private assignment without saving it"),
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Read or review a person's client routing override

    --inherit follows access-profile and fleet defaults. This private service setting
    survives applies and reissue; it changes delivery after the next app refresh.

    [dim]Examples:[/dim]
      [cyan]meridian client routing person[/cyan]
      [cyan]meridian client routing person full --dry-run --json[/cyan]
      [cyan]meridian client routing person --inherit --yes[/cyan]
    """
    from meridian.commands.client_routing import run_routing

    run_routing(person, policy=policy, inherit=inherit, dry_run=dry_run, yes=yes)
