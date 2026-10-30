"""Operator notice commands mounted on the fleet group."""

from __future__ import annotations

import typer

from meridian.cli_options import Json, Yes

notice_app = typer.Typer(help="Preview, publish, or clear one notice for everyone", no_args_is_help=True)


@notice_app.command("show", rich_help_panel="Notice")
def notice_show_cmd(
    for_user: str | None = typer.Option(None, "--for", metavar="NAME", help="Render the notice for this person"),
    _json: Json = False,
) -> None:
    """Show the saved notice and a rendered preview"""
    from meridian.commands.fleet_notice import run_notice

    run_notice("show", for_user=for_user)


@notice_app.command("set", rich_help_panel="Notice")
def notice_set_cmd(
    text: str = typer.Argument(..., metavar="TEXT", help="One plain paragraph, up to 500 characters"),
    until: str | None = typer.Option(
        None, "--until", metavar="WHEN", help="Future UTC time or date; omit for no expiry"
    ),
    for_user: str | None = typer.Option(None, "--for", metavar="NAME", help="Preview for this person before saving"),
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Preview and confirm the owner's notice for apps and connection pages"""
    from meridian.commands.fleet_notice import run_notice

    run_notice("set", text=text, until=until, for_user=for_user, yes=yes)


@notice_app.command("clear", rich_help_panel="Notice")
def notice_clear_cmd(yes: Yes = False, _json: Json = False) -> None:
    """Confirm removal of the notice from apps and connection pages"""
    from meridian.commands.fleet_notice import run_notice

    run_notice("clear", yes=yes)
