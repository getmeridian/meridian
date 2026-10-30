"""The `meridian context` group: several fleets on one machine."""

from __future__ import annotations

import typer

from meridian.cli_options import Json, Yes

context_app = typer.Typer(
    help="Keep several fleets on one machine and switch between them: list, use, rename, remove", no_args_is_help=True
)


@context_app.command("list")
def context_list_cmd(
    _json: Json = False,
) -> None:
    """List your fleets, marking the current one with *

    Reads only what this machine saved; no server is contacted.

    [dim]Examples:[/dim]
      [cyan]meridian context list[/cyan]
    """
    from meridian.commands.context import run_list

    run_list()


@context_app.command("use")
def context_use_cmd(
    name: str = typer.Argument(..., metavar="NAME", help="Context to make current"),
) -> None:
    """Make NAME the fleet every command works on

    [dim]Examples:[/dim]
      [cyan]meridian context use family[/cyan]
      [cyan]meridian --context default client list[/cyan]   One command on another fleet
      [cyan]meridian deploy 203.0.113.5 --context work[/cyan]   Deploying creates a new context
    """
    from meridian.commands.context import run_use

    run_use(name)


@context_app.command("rename")
def context_rename_cmd(
    old: str = typer.Argument(..., metavar="OLD", help="Current name"),
    new: str = typer.Argument(..., metavar="NEW", help="New name"),
) -> None:
    """Rename a context; its fleet and servers are untouched

    [dim]Examples:[/dim]
      [cyan]meridian context rename work office[/cyan]
    """
    from meridian.commands.context import run_rename

    run_rename(old, new)


@context_app.command("remove")
def context_remove_cmd(
    name: str = typer.Argument(..., metavar="NAME", help="Context to remove"),
    yes: Yes = False,
) -> None:
    """Delete a context's saved files from this machine; its servers keep running

    Refuses the current context. A fleet that still has servers needs confirmation:
    only this machine's copy goes, and those servers can no longer be managed from here
    unless you restore the files.

    [dim]Examples:[/dim]
      [cyan]meridian context remove family[/cyan]
    """
    from meridian.commands.context import run_remove

    run_remove(name, yes=yes)
