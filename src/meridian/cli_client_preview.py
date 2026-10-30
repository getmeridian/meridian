"""Transient subscription request inputs for the client's format explanation."""

from __future__ import annotations

import typer

from meridian.cli_options import Json


def client_preview_cmd(
    name: str = typer.Argument(..., metavar="NAME", help="Client name"),
    user_agent: str = typer.Option(..., "--user-agent", metavar="UA", help="App User-Agent, up to 512 characters"),
    accept: str = typer.Option("", "--accept", metavar="TYPE", help="Accept header, up to 256 characters"),
    suffix: str = typer.Option(
        "", "--suffix", metavar="SUFFIX", help="Force info, json, v2ray-json, mihomo, clash, stash, or singbox"
    ),
    _json: Json = False,
) -> None:
    """Explain the chosen subscription format and omitted entries without credentials"""
    from meridian.commands.client_preview import run_preview

    run_preview(name, user_agent=user_agent, accept=accept, suffix=suffix)
