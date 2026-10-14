from __future__ import annotations

import click
from typer.testing import CliRunner

from meridian.cli import app
from meridian.commands.help import load_agent_guide

runner = CliRunner()


def test_help_agents_prints_the_bundled_guide_as_plain_markdown() -> None:
    result = runner.invoke(app, ["help", "agents"])

    assert result.exit_code == 0
    assert result.stdout.startswith("# Meridian for AI agents")
    assert result.stdout == load_agent_guide()
    assert "\x1b[" not in result.stdout


def test_help_command_path_shows_that_commands_help() -> None:
    result = runner.invoke(app, ["help", "node", "add"])
    output = click.unstyle(result.stdout)  # CI forces color, which splits "--name" with style codes

    assert result.exit_code == 0
    assert "meridian node add" in output
    assert "--name" in output


def test_bare_help_matches_the_top_level_help() -> None:
    result = runner.invoke(app, ["help"])

    assert result.exit_code == 0
    assert "Get started" in result.stdout
    assert "meridian help agents" in result.stdout


def test_unknown_and_hidden_topics_are_user_errors() -> None:
    for topic in (["nope"], ["dev"], ["node", "nope"]):
        result = runner.invoke(app, ["help", *topic])
        assert result.exit_code == 2, topic


def test_agent_guide_names_only_real_commands() -> None:
    import re

    import typer.main

    from meridian.cli import app as root

    group = typer.main.get_command(root)
    for line in load_agent_guide().splitlines():
        for match in re.finditer(r"`meridian ([a-z]+)", line):
            assert match.group(1) in group.commands, match.group(0)  # type: ignore[attr-defined]
