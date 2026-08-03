"""Validate the public CLI command tree against every CLI reference locale.

Usage:
    uv run python tests/validate_cli_docs.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import click
from typer.main import get_command

ROOT = Path(__file__).resolve().parent.parent

CLI_REFERENCES = {
    "en": ROOT / "website" / "src" / "content" / "docs" / "en" / "cli-reference.md",
    "ru": ROOT / "website" / "src" / "content" / "docs" / "ru" / "cli-reference.md",
    "fa": ROOT / "website" / "src" / "content" / "docs" / "fa" / "cli-reference.md",
    "zh": ROOT / "website" / "src" / "content" / "docs" / "zh" / "cli-reference.md",
}

# Public leaf commands and the section that owns their flags. Keeping this map
# explicit makes section moves deliberate; discovery below makes new or removed
# commands fail validation instead of silently falling outside the audit.
COMMAND_SECTIONS: dict[str, str] = {
    "studio": "### meridian studio",
    "plan": "### meridian plan",
    "apply": "### meridian apply",
    "setup": "### meridian setup",
    "deploy": "### meridian deploy",
    "client add": "### meridian client",
    "client show": "### meridian client",
    "client list": "### meridian client",
    "client remove": "### meridian client",
    "client enable": "### meridian client",
    "client disable": "### meridian client",
    "server add": "### meridian server",
    "server list": "### meridian server",
    "server remove": "### meridian server",
    "preflight": "### meridian preflight",
    "scan": "### meridian scan",
    "test": "### meridian test",
    "probe": "### meridian probe",
    "doctor": "### meridian doctor",
    "teardown": "### meridian teardown",
    "update": "### meridian update",
    "relay deploy": "### meridian relay",
    "relay list": "### meridian relay",
    "relay remove": "### meridian relay",
    "relay check": "### meridian relay",
    "node add": "### meridian node",
    "node list": "### meridian node",
    "node check": "### meridian node",
    "node remove": "### meridian node",
    "fleet status": "### meridian fleet",
    "fleet inventory": "### meridian fleet",
    "fleet recover": "### meridian fleet",
    "api schemas": "### meridian api",
    "api commands": "### meridian api",
    "api schema": "### meridian api",
    "api workflow": "### meridian api",
}

GLOBAL_SECTIONS = {
    "en": "## Global options",
    "ru": "## Глобальные параметры",
    "fa": "## گزینه‌های سراسری",
    "zh": "## 全局选项",
}


def public_leaf_commands() -> tuple[click.Command, dict[str, click.Command]]:
    """Return the root Click command and every non-hidden public leaf."""
    from meridian.cli import app

    root = get_command(app)
    leaves: dict[str, click.Command] = {}

    def walk(group: click.Group, prefix: tuple[str, ...] = ()) -> None:
        for name, command in group.commands.items():
            if command.hidden:
                continue
            path = (*prefix, name)
            if isinstance(command, click.Group):
                walk(command, path)
            else:
                leaves[" ".join(path)] = command

    if not isinstance(root, click.Group):
        raise RuntimeError("Meridian CLI root is not a Click group")
    walk(root)
    return root, leaves


def long_flags(command: click.Command) -> set[str]:
    """Return every visible long-form option exposed by a Click command."""
    flags: set[str] = set()
    for parameter in command.params:
        if not isinstance(parameter, click.Option) or parameter.hidden:
            continue
        for option in (*parameter.opts, *parameter.secondary_opts):
            if option.startswith("--"):
                flags.add(option)
    return flags


def parse_doc_sections(path: Path) -> dict[str, str]:
    """Split a CLI reference into second- and third-level sections."""
    sections: dict[str, str] = {}
    current_heading = ""
    current_lines: list[str] = []

    for line in path.read_text().splitlines():
        if line.startswith(("### ", "## ")):
            if current_heading:
                sections[current_heading] = "\n".join(current_lines)
            current_heading = line.strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_heading:
        sections[current_heading] = "\n".join(current_lines)
    return sections


def flags_in_section(section_text: str) -> set[str]:
    """Extract long-form option references from one documentation section."""
    return set(re.findall(r"--[a-z][\w-]*", section_text))


def main() -> int:
    errors: list[str] = []
    root, discovered = public_leaf_commands()

    documented_commands = set(COMMAND_SECTIONS)
    discovered_commands = set(discovered)
    for command in sorted(discovered_commands - documented_commands):
        errors.append(f"  public command has no documentation mapping: meridian {command}")
    for command in sorted(documented_commands - discovered_commands):
        errors.append(f"  documentation mapping is stale: meridian {command}")

    root_flags = long_flags(root)
    for locale, path in CLI_REFERENCES.items():
        if not path.exists():
            errors.append(f"  {locale}: {path.relative_to(ROOT)} not found")
            continue

        sections = parse_doc_sections(path)
        global_heading = GLOBAL_SECTIONS[locale]
        global_text = sections.get(global_heading)
        if global_text is None:
            errors.append(f"  {locale}: section '{global_heading}' not found")
        else:
            for flag in sorted(root_flags - flags_in_section(global_text)):
                errors.append(f"  {locale}: root option {flag} not documented in '{global_heading}'")

        for command in sorted(discovered_commands & documented_commands):
            heading = COMMAND_SECTIONS[command]
            section_text = sections.get(heading)
            if section_text is None:
                errors.append(f"  {locale}: section '{heading}' not found")
                continue
            for flag in sorted(long_flags(discovered[command]) - flags_in_section(section_text)):
                errors.append(f"  {locale}: meridian {command}: {flag} not documented in '{heading}'")

    if errors:
        print("ERROR: CLI documentation drift found:\n")
        print("\n".join(errors))
        return 1

    print(f"OK: Public CLI documented in all locales ({len(discovered)} commands, {len(root_flags)} global options)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
