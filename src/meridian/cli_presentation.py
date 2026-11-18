"""Public CLI for the fleet's subscription title and refresh interval."""

import typer

from meridian.cli_options import Json, Yes


def title_cmd(
    title: str | None = typer.Argument(None, help="Subscription title; omit to read"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview the change"),
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Read or edit the title shown in subscribers' apps"""
    from meridian.commands.fleet_presentation import run_presentation_value

    run_presentation_value("profile_title", title, dry_run=dry_run, yes=yes)


def refresh_cmd(
    hours: int | None = typer.Argument(None, min=1, max=168, help="Hours between app refreshes; omit to read"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview the change"),
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Read or edit the interval between subscription refreshes"""
    from meridian.commands.fleet_presentation import run_presentation_value

    run_presentation_value("update_interval_hours", hours, dry_run=dry_run, yes=yes)
