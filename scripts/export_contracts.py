"""Export deterministic meridian-core contracts for API clients."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from meridian.cli_catalog import command_catalog
from meridian.control.openapi import openapi_document
from meridian.core.agent_http import AGENT_HTTP_SURFACE
from meridian.core.schema import (
    event_type_catalog,
    schema_for,
    schema_names,
)
from meridian.core.schema_agent import AGENT_SCHEMAS, agent_schema

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "contracts" / "meridian" / "v1"


def _plain_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def build_contract_files() -> dict[Path, str]:
    """Return contract file paths relative to the output directory."""
    files: dict[Path, str] = {}

    for name in schema_names():
        files[Path("schemas") / f"{name}.schema.json"] = _plain_json(schema_for(name))

    files[Path("commands.json")] = _plain_json({"schema": "meridian.command-catalog/v1", "commands": command_catalog()})
    files[Path("events.json")] = _plain_json(event_type_catalog())
    files[Path("admin") / "openapi.json"] = _plain_json(openapi_document().to_data())
    # The service-to-agent protocol is internal (consumed by the Rust agent), so it stays out of `schemas/`.
    for name in sorted(AGENT_SCHEMAS):
        files[Path("agent") / "schemas" / f"{name}.schema.json"] = _plain_json(agent_schema(name))
    files[Path("agent") / "http.json"] = _plain_json(AGENT_HTTP_SURFACE.to_data())

    return dict(sorted(files.items(), key=lambda item: item[0].as_posix()))


def write_contracts(output_dir: Path) -> None:
    files = build_contract_files()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    for relative, content in files.items():
        path = output_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def check_contracts(output_dir: Path) -> list[str]:
    expected = build_contract_files()
    errors: list[str] = []

    actual_paths = (
        {path.relative_to(output_dir) for path in output_dir.rglob("*") if path.is_file()}
        if output_dir.exists()
        else set()
    )
    expected_paths = set(expected)

    for missing in sorted(expected_paths - actual_paths):
        errors.append(f"missing: {missing.as_posix()}")
    for extra in sorted(actual_paths - expected_paths):
        errors.append(f"extra: {extra.as_posix()}")
    for relative in sorted(expected_paths & actual_paths):
        actual = (output_dir / relative).read_text(encoding="utf-8")
        if actual != expected[relative]:
            errors.append(f"changed: {relative.as_posix()}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if checked-in contracts are out of date")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output directory")
    args = parser.parse_args(argv)

    output_dir = args.output.resolve()
    if args.check:
        errors = check_contracts(output_dir)
        if errors:
            print("Contract artifacts are out of date:", file=sys.stderr)
            for error in errors:
                print(f"  {error}", file=sys.stderr)
            print("Run: uv run python scripts/export_contracts.py", file=sys.stderr)
            return 1
        print(f"OK: contracts current in {output_dir}")
        return 0

    write_contracts(output_dir)
    print(f"Wrote contracts to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
