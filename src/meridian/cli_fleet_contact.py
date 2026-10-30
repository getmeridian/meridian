"""CLI arguments for reviewed public owner contact."""

from __future__ import annotations

import typer

from meridian.cli_options import Json, Yes


def fleet_contact_cmd(
    text: str | None = typer.Option(
        None, "--text", metavar="TEXT", help='Public instructions, up to 120 characters; "" clears'
    ),
    link: str | None = typer.Option(
        None, "--link", metavar="LINK", help="https:, tg:, or mailto: contact link; none clears"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Review the contact change without applying it"),
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Show or review the owner's public contact for connection pages and apps

    Only supplied fields change. A change is reviewed and confirmed before apply.

    [dim]Examples:[/dim]
      [cyan]meridian fleet contact[/cyan]
      [cyan]meridian fleet contact --text "Ask for help" --link mailto:help@example.com --dry-run --json[/cyan]
      [cyan]meridian fleet contact --text "" --link none --yes[/cyan]
    """
    from meridian.commands.fleet_contact import run_contact

    run_contact(text=text, link=link, dry_run=dry_run, yes=yes)
