"""The command catalog is read from the Typer tree; core keeps only what Typer cannot know."""

from __future__ import annotations

from meridian.cli_catalog import command_argv, command_contracts, long_options, public_commands
from meridian.core.command_catalog import COMMAND_OUTPUTS


def test_every_public_command_has_an_output_mapping() -> None:
    assert sorted(set(public_commands()) - set(COMMAND_OUTPUTS)) == []


def test_every_output_mapping_names_a_public_command() -> None:
    assert sorted(set(COMMAND_OUTPUTS) - set(public_commands())) == []


def test_json_contracts_are_exactly_the_commands_that_declare_json() -> None:
    for path, command in public_commands().items():
        assert ("--json" in long_options(command)) == (COMMAND_OUTPUTS[path] is not None), path


def test_contracts_take_argv_flags_and_help_from_the_command() -> None:
    commands = public_commands()
    contracts = {contract.command: contract for contract in command_contracts()}

    route_add = contracts["route.add"]
    assert route_add.argv == ["route", "add", "EXIT", "[VALUE...]"]
    assert route_add.machine_flags == ["--via", "--preset", "--name", "--json"]
    assert route_add.description == "Send matching destinations through WARP, directly, or nowhere"
    assert command_argv("check", commands["check"]) == ["check", "[SERVER]"]
    assert "dev.preview" not in contracts
