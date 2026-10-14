"""The landing page's command builder only emits commands and flags the CLI has."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meridian.cli_catalog import long_options, public_commands
from meridian.core.inputs import PAGE_PALETTES

BUILDER = json.loads(
    (Path(__file__).resolve().parent.parent / "website" / "src" / "data" / "command-builder.json").read_text()
)
COMMANDS = public_commands()
TABS = BUILDER["tabs"]


def _command(tab: dict) -> str:
    return tab["command"].replace(" ", ".")


@pytest.mark.parametrize("tab", TABS, ids=[tab["id"] for tab in TABS])
def test_builder_tab_names_a_public_command(tab: dict) -> None:
    assert _command(tab) in COMMANDS


@pytest.mark.parametrize("tab", TABS, ids=[tab["id"] for tab in TABS])
def test_builder_tab_flags_exist_on_its_command(tab: dict) -> None:
    flags = set(long_options(COMMANDS[_command(tab)], include_hidden=False))
    missing = [BUILDER["fields"][field]["flag"] for field in tab["fields"]]
    assert sorted(flag for flag in missing if flag not in flags) == []


def test_builder_tabs_only_use_declared_fields() -> None:
    used = {field for tab in TABS for field in tab["fields"]}
    assert sorted(used - set(BUILDER["fields"])) == []


def test_builder_palettes_match_connection_page_palettes() -> None:
    assert tuple(BUILDER["fields"]["color"]["palettes"]) == PAGE_PALETTES
