"""`meridian help` — the agent guide, or one command's help, git-style."""

from __future__ import annotations

import sys
from importlib.resources import files as pkg_files

import click
import typer

from meridian.console import fail

AGENT_TOPICS = frozenset({"agents", "agent", "ai"})


def load_agent_guide() -> str:
    """The bundled guide that explains Meridian to AI agents."""
    return (pkg_files("meridian") / "data" / "agents.md").read_text(encoding="utf-8")


def run(ctx: typer.Context, topic: list[str]) -> None:
    root = ctx.find_root()
    if not topic:
        typer.echo(root.get_help())
        return
    if len(topic) == 1 and topic[0].lower() in AGENT_TOPICS:
        # Plain Markdown, never Rich: agents read it from a pipe as often as people read it in a terminal.
        sys.stdout.write(load_agent_guide())
        return

    command: click.Command = root.command
    command_ctx = root
    for name in topic:
        sub = command.get_command(command_ctx, name) if isinstance(command, click.Group) else None
        if sub is None or sub.hidden:
            path = " ".join(topic)
            fail(
                f"No help topic or command named '{path}'",
                hint="Run meridian help agents for the agent guide, or meridian --help for every command.",
                hint_type="user",
            )
        command_ctx = click.Context(sub, info_name=name, parent=command_ctx)
        command = sub
    typer.echo(command.get_help(command_ctx))
