"""Thin Typer entry point for signed node operations."""

from __future__ import annotations

import typer

from meridian.cli_options import Json, Yes


def node_restart_cmd(
    name: str = typer.Argument(..., metavar="NAME", help="Node name"),
    core: bool = typer.Option(False, "--core", help="Restart Xray (netcore); the default"),
    all_units: bool = typer.Option(
        False, "--all", help="Restart every running unit managed by the node's held document"
    ),
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Restart Xray or every managed unit on a node

    Default and --core restart Xray. --all restarts each running managed unit, one at a time.
    Active connections drop while their unit restarts. Waits up to five minutes for typed per-node results.

    [dim]Examples:[/dim]
      [cyan]meridian node restart main --yes --json[/cyan]
      [cyan]meridian node restart main --all --yes[/cyan]
    """
    from meridian.commands.node_restart import run_restart

    run_restart(name, core=core, all_units=all_units, yes=yes)
