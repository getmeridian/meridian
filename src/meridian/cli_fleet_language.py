"""An explicit operator default for native subscription status remarks."""

from __future__ import annotations

import typer

from meridian.cli_options import Json


def fleet_language_cmd(
    language: str | None = typer.Argument(None, metavar="LANGUAGE", help="en, ru, fa, or zh; omit to read"),
    _json: Json = False,
) -> None:
    """Read or set the fleet's subscription status language"""
    from meridian.commands.fleet_language import run_language

    run_language(language)
