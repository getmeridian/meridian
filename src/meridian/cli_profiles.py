"""Access profiles are topology edits; their memberships remain service-owned."""

from __future__ import annotations

import typer

from meridian.cli_options import Json, Yes

profile_app = typer.Typer(help="Review which exits and paths each access profile grants", no_args_is_help=True)


@profile_app.command("add", rich_help_panel="Reviewed changes")
def profile_add(
    name: str = typer.Argument(..., metavar="PROFILE", help="New profile name"),
    grant: list[str] | None = typer.Option(
        None, "--grant", metavar="REFERENCE", help="Repeat for each exit:, gateway:, inbound:, or endpoint: reference"
    ),
    empty: bool = typer.Option(False, "--empty", help="Create a profile with no grants"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Review topology and who gains or loses access"),
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Add a named profile after reviewing its authentication grants."""
    from meridian.commands.profiles import run_change

    run_change("add", name, grant, empty=empty, dry_run=dry_run, yes=yes)


@profile_app.command("set", rich_help_panel="Reviewed changes")
def profile_set(
    name: str = typer.Argument(..., metavar="PROFILE", help="Profile to edit"),
    grant: list[str] | None = typer.Option(
        None, "--grant", metavar="REFERENCE", help="Replace grants; repeat for every reference to keep"
    ),
    empty: bool = typer.Option(False, "--empty", help="Remove every grant from this profile"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Review topology and who gains or loses access"),
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Replace a profile's grants; every member's effective access is reviewed."""
    from meridian.commands.profiles import run_change

    run_change("set", name, grant, empty=empty, dry_run=dry_run, yes=yes)


@profile_app.command("remove", rich_help_panel="Reviewed changes")
def profile_remove(
    name: str = typer.Argument(..., metavar="PROFILE", help="Unused profile to remove"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Check membership and review removal"),
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Remove an unused profile; default always grants the whole fleet."""
    from meridian.commands.profiles import run_change

    run_change("remove", name, None, empty=False, dry_run=dry_run, yes=yes)


@profile_app.command("list", rich_help_panel="Read")
def profile_list(_json: Json = False) -> None:
    """List reviewed grants and service-owned membership counts."""
    from meridian.commands.profiles import run_list

    run_list()
