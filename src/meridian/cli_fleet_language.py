"""The operator default for connection pages and native app messages."""

from __future__ import annotations

import typer

from meridian.cli_options import Json, Yes


def fleet_language_cmd(
    language: str | None = typer.Argument(
        None, metavar="LANGUAGE", help="auto follows browsers; en, ru, fa, or zh sets a default; omit to read"
    ),
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Read or set the default language for pages and app messages"""
    from meridian.commands.fleet_language import run_language

    run_language(language, yes=yes)
