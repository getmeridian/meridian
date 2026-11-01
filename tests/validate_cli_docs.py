"""Validate the public CLI command tree against every CLI reference locale.

Usage:
    uv run python tests/validate_cli_docs.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from meridian import cli_types
from meridian.cli_catalog import cli_root, long_options, public_commands

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
    "plan": "### meridian plan",
    "apply": "### meridian apply",
    "setup": "### meridian setup",
    "deploy": "### meridian deploy",
    "help": "### meridian help",
    "client add": "### meridian client",
    "client show": "### meridian client",
    "client preview": "### meridian client",
    "client usage": "### meridian client",
    "client list": "### meridian client",
    **{
        f"client bulk {action}": "### meridian client"
        for action in (
            "extend",
            "expiry",
            "quota",
            "reset-traffic",
            "enable",
            "disable",
            "tag",
            "devices",
            "renewal",
            "delete",
            "undo",
            "receipt",
        )
    },
    "client set": "### meridian client",
    "client reset-traffic": "### meridian client",
    "client extend": "### meridian client",
    "client devices": "### meridian client",
    "client reissue": "### meridian client",
    "client rotate-credentials": "### meridian client",
    "client remove": "### meridian client",
    "client enable": "### meridian client",
    "client disable": "### meridian client",
    "server add": "### meridian server",
    "server list": "### meridian server",
    "server set": "### meridian server",
    "server remove": "### meridian server",
    "check": "### meridian check",
    "scan": "### meridian scan",
    "test": "### meridian test",
    "probe": "### meridian test",
    "doctor": "### meridian doctor",
    "logs": "### meridian logs",
    "panel": "### meridian panel",
    "privacy show": "### meridian privacy",
    "privacy set": "### meridian privacy",
    "teardown": "### meridian teardown",
    "backup pull": "### meridian backup",
    "backup recovery-key": "### meridian backup",
    "backup devices": "### meridian backup",
    "backup revoke": "### meridian backup",
    "restore": "### meridian restore",
    "migrate": "### meridian migrate",
    "update": "### meridian update",
    "relay add": "### meridian relay",
    "relay list": "### meridian relay",
    "relay remove": "### meridian relay",
    "relay sni": "### meridian relay",
    "route add": "### meridian route",
    "route list": "### meridian route",
    "route remove": "### meridian route",
    "node add": "### meridian node",
    "node list": "### meridian node",
    "node restart": "### meridian node",
    "node remove": "### meridian node",
    "node replace": "### meridian node",
    "node sni": "### meridian node",
    "fleet status": "### meridian fleet",
    "fleet inventory": "### meridian fleet",
    "fleet notice show": "### meridian fleet",
    "fleet notice set": "### meridian fleet",
    "fleet notice clear": "### meridian fleet",
    "fleet language": "### meridian fleet",
    "fleet upgrade": "### meridian fleet",
    "fleet export": "### meridian fleet",
    "fleet import": "### meridian fleet",
    "fleet history": "### meridian fleet",
    "fleet events": "### meridian fleet",
    "fleet usage": "### meridian fleet",
    "fleet routing": "### meridian fleet",
    "fleet contact": "### meridian fleet",
    "context list": "### meridian context",
    "context use": "### meridian context",
    "context rename": "### meridian context",
    "context remove": "### meridian context",
    "api schemas": "### meridian api",
    "api commands": "### meridian api",
    "api schema": "### meridian api",
    "api workflow": "### meridian api",
}

# Root options every leaf also accepts (`cli_options.add_context_option`): documented once, globally.
LEAF_GLOBALS = {"--context"}

GLOBAL_SECTIONS = {
    "en": "## Global options",
    "ru": "## Глобальные параметры",
    "fa": "## گزینه‌های سراسری",
    "zh": "## 全局选项",
}


def long_flags(command: cli_types.Command) -> set[str]:
    """Return every visible long-form option exposed by a Click command."""
    return set(long_options(command, include_hidden=False))


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
    root = cli_root()
    discovered = {path.replace(".", " "): command for path, command in public_commands(root).items()}

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
            for flag in sorted(long_flags(discovered[command]) - LEAF_GLOBALS - flags_in_section(section_text)):
                errors.append(f"  {locale}: meridian {command}: {flag} not documented in '{heading}'")

    if errors:
        print("ERROR: CLI documentation drift found:\n")
        print("\n".join(errors))
        return 1

    print(f"OK: Public CLI documented in all locales ({len(discovered)} commands, {len(root_flags)} global options)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
