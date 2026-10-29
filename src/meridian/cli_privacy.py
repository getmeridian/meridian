"""The public privacy command group; bare `privacy` is the `show` action."""

from __future__ import annotations

from typing import Annotated

import typer

from meridian.cli_options import Json, Yes

privacy_app = typer.Typer(help="Choose what device limits and usage history store", no_args_is_help=False)


@privacy_app.callback(invoke_without_command=True)
def privacy_default(ctx: typer.Context, _json: Json = False) -> None:
    if ctx.invoked_subcommand is None:
        from meridian.commands.privacy import run_show

        run_show()


@privacy_app.command("show")
def privacy_show(_json: Json = False) -> None:
    """Show privacy levels, their disclosures, and the last changes"""
    from meridian.commands.privacy import run_show

    run_show()


@privacy_app.command("set")
def privacy_set(
    devices: Annotated[
        str | None, typer.Option("--devices", metavar="LEVEL", help="Device limits: off, private, full")
    ] = None,
    device_limit: Annotated[
        str | None, typer.Option("--device-limit", metavar="N", help="Default per-person device cap, 1–100")
    ] = None,
    history: Annotated[str | None, typer.Option("--history", metavar="LEVEL", help="Usage history: off, daily")] = None,
    history_days: Annotated[
        str | None, typer.Option("--history-days", metavar="N", help="Retained UTC days, 7–730")
    ] = None,
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Change privacy settings after reviewing what will be stored or deleted

    Only options you pass change. Raising a level prints its privacy cost; lowering
    it removes the old records in the same transaction as the setting and audit entry.

    [dim]Examples:[/dim]
      [cyan]meridian privacy set --history daily --history-days 90[/cyan]
      [cyan]meridian privacy set --devices private --device-limit 3 --yes[/cyan]
      [cyan]meridian privacy set --devices off --history off --yes --json[/cyan]
    """
    from meridian.commands.privacy import run_set

    run_set(devices=devices, device_limit=device_limit, history=history, history_days=history_days, yes=yes)
