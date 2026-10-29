"""The hidden service entry point, mounted by the CLI and called by systemd."""

from __future__ import annotations

import typer


def serve_cmd(
    config: str = typer.Option("/etc/ctld/config.json", "--config", help="Service configuration file"),
) -> None:
    """Run the control service (on the control server, under systemd)"""
    from pathlib import Path

    from meridian.control.server import serve

    serve(Path(config))
