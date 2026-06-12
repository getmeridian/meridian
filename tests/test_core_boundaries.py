"""Boundary tests for meridian-core module ownership."""

from __future__ import annotations

import ast
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parents[1] / "src" / "meridian" / "core"
ENGINE_DIR = Path(__file__).resolve().parents[1] / "src" / "meridian" / "engine"
ADAPTERS_DIR = Path(__file__).resolve().parents[1] / "src" / "meridian" / "adapters"
FORBIDDEN_IMPORTS = (
    "meridian.commands",
    "meridian.console",
    "rich",
    "typer",
)
ENGINE_FORBIDDEN_IMPORTS = FORBIDDEN_IMPORTS + (
    "meridian.provision",
    "meridian.remnawave",
    "meridian.renderers",
    "meridian.ssh",
)
ADAPTER_FORBIDDEN_IMPORTS = (
    "meridian.engine",
)


def _imported_module_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_core_does_not_import_cli_or_rendering_modules() -> None:
    violations: list[str] = []
    for path in sorted(CORE_DIR.rglob("*.py")):
        for module in _imported_module_names(path):
            if any(module == forbidden or module.startswith(f"{forbidden}.") for forbidden in FORBIDDEN_IMPORTS):
                violations.append(f"{path.relative_to(CORE_DIR)} imports {module}")

    assert violations == []


def test_engine_does_not_import_commands_or_runtime_adapters() -> None:
    violations: list[str] = []
    for path in sorted(ENGINE_DIR.rglob("*.py")):
        for module in _imported_module_names(path):
            if any(module == forbidden or module.startswith(f"{forbidden}.") for forbidden in ENGINE_FORBIDDEN_IMPORTS):
                violations.append(f"{path.relative_to(ENGINE_DIR)} imports {module}")

    assert violations == []


def test_adapters_do_not_import_engine_modules() -> None:
    violations: list[str] = []
    for path in sorted(ADAPTERS_DIR.rglob("*.py")):
        for module in _imported_module_names(path):
            if any(module == forbidden or module.startswith(f"{forbidden}.") for forbidden in ADAPTER_FORBIDDEN_IMPORTS):
                violations.append(f"{path.relative_to(ADAPTERS_DIR)} imports {module}")

    assert violations == []
