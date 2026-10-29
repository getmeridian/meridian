"""The fleet's daily usage command, mounted on the fleet group."""

from __future__ import annotations

import typer

from meridian.cli_options import Json


def fleet_usage_cmd(
    days: str = typer.Option("30", "--days", metavar="N", help="UTC days to show, 1–730, within the retention cap"),
    _json: Json = False,
) -> None:
    """Show daily fleet and server usage when daily history is on"""
    from meridian.commands.fleet_usage import run_usage

    run_usage(days=days)
