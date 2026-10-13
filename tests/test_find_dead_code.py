"""Contracts for scripts/find_dead_code.py on small synthetic trees."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from scripts.find_dead_code import analyze, main

PYPROJECT = """\
[project]
name = "synthetic"

[project.scripts]
meridian = "meridian.cli:app"
"""

CLI = """\
import typer

from meridian.drivers import run
from meridian.models import Request

app = typer.Typer()


@app.command()
def deploy(name: str, verbose: bool = False) -> None:
    run(Request(name=name))
"""

MODELS = """\
from pydantic import BaseModel, field_validator


class Request(BaseModel):
    name: str
    unused_field: int = 0

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()
"""

DRIVERS = """\
from typing import Protocol

from meridian import registry
from meridian.models import Request


class Driver(Protocol):
    def observe(self, request: Request) -> str: ...


class NginxDriver:
    def observe(self, request: Request) -> str:
        return request.name

    def forgotten(self) -> None:
        pass


def run(request: Request) -> str:
    driver: Driver = NginxDriver()
    getattr(registry, "build")()
    handler = registry.Handlers()
    getattr(handler, "on_ping")()
    return driver.observe(request) + helper(request, 1)


def helper(request: Request, retries: int) -> str:
    return request.name


def dead_helper() -> int:
    return dead_inner()


def dead_inner() -> int:
    return 1


def tested_helper() -> int:
    return tested_inner()


def tested_inner() -> int:
    return 2
"""

REGISTRY = """\
def build() -> None:
    pass


class Handlers:
    def on_ping(self) -> None:
        pass
"""

TEST_DRIVERS = """\
from meridian.drivers import tested_helper


def test_tested_helper() -> None:
    assert tested_helper() == 2
"""


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    _write(tmp_path, "pyproject.toml", PYPROJECT)
    _write(tmp_path, "src/meridian/__init__.py", "")
    _write(tmp_path, "src/meridian/cli.py", CLI)
    _write(tmp_path, "src/meridian/models.py", MODELS)
    _write(tmp_path, "src/meridian/drivers.py", DRIVERS)
    _write(tmp_path, "src/meridian/registry.py", REGISTRY)
    _write(tmp_path, "tests/test_drivers.py", TEST_DRIVERS)
    return tmp_path


def _by_category(root: Path) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for finding in analyze(root):
        found.setdefault(finding.category, set()).add(finding.name)
    return found


def _all_names(root: Path) -> set[str]:
    return {finding.name for finding in analyze(root)}


def test_unreferenced_function_and_its_private_callee_are_dead(tree: Path) -> None:
    dead = _by_category(tree)["dead"]
    assert {"meridian.drivers.dead_helper", "meridian.drivers.dead_inner"} <= dead


def test_method_nobody_calls_is_dead(tree: Path) -> None:
    assert "meridian.drivers.NginxDriver.forgotten" in _by_category(tree)["dead"]


def test_code_kept_alive_only_by_its_tests_is_test_only(tree: Path) -> None:
    test_only = _by_category(tree)["test-only"]
    assert test_only == {"meridian.drivers.tested_helper", "meridian.drivers.tested_inner"}


def test_typer_command_and_entry_point_are_not_flagged(tree: Path) -> None:
    names = _all_names(tree)
    assert "meridian.cli.deploy" not in names
    assert "meridian.cli.app" not in names
    assert not any(name.startswith("meridian.cli.deploy(") for name in names)


def test_pydantic_fields_and_validators_are_not_flagged(tree: Path) -> None:
    names = _all_names(tree)
    assert "meridian.models.Request.unused_field" not in names
    assert "meridian.models.Request._strip" not in names


def test_protocol_method_and_its_implementation_are_not_flagged(tree: Path) -> None:
    names = _all_names(tree)
    assert "meridian.drivers.Driver.observe" not in names
    assert "meridian.drivers.NginxDriver.observe" not in names
    assert "meridian.drivers.NginxDriver.observe(request)" not in names


def test_string_referenced_names_are_not_flagged(tree: Path) -> None:
    names = _all_names(tree)
    assert "meridian.registry.build" not in names
    assert "meridian.registry.Handlers.on_ping" not in names


def test_production_string_does_not_keep_a_module_function_alive(tree: Path) -> None:
    _write(tree, "src/meridian/labels.py", 'STAGES = ("connect", "dead_helper")\n')
    _write(tree, "src/meridian/cli.py", CLI + "\nfrom meridian.labels import STAGES\nprint(STAGES)\n")
    assert "meridian.drivers.dead_helper" in _by_category(tree)["dead"]


def test_unused_parameter_of_live_function_is_reported(tree: Path) -> None:
    assert _by_category(tree)["unused-param"] == {"meridian.drivers.helper(retries)"}


def test_allowlist_suppresses_entry_and_reports_stale_ones(tree: Path) -> None:
    _write(
        tree,
        "scripts/dead_code_allowlist.txt",
        """\
        # comment lines are ignored
        meridian.drivers.dead_helper  # loaded by a plugin hook
        meridian.drivers.removed_long_ago  # used to be dynamic
        meridian.drivers.tested_helper
        meridian.drivers.NginxDriver  # a class entry covers its members
        """,
    )
    findings = analyze(tree)
    names = {(f.category, f.name) for f in findings}
    assert ("dead", "meridian.drivers.dead_helper") not in names
    # An allowlisted name is a production root, so its callees stay quiet too.
    assert ("dead", "meridian.drivers.dead_inner") not in names
    assert ("stale-allowlist", "meridian.drivers.removed_long_ago") in names
    assert ("allowlist-no-reason", "meridian.drivers.tested_helper") in names
    assert ("dead", "meridian.drivers.NginxDriver.forgotten") not in names
    stale = next(f for f in findings if f.category == "stale-allowlist")
    assert (stale.path, stale.line) == ("scripts/dead_code_allowlist.txt", 3)


def test_output_is_sorted_and_exit_code_follows_strict(
    tree: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert main(["--root", str(tree)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == "dead-code: 3 dead, 2 test-only, 1 unused-param"
    assert lines[:-1] == sorted(lines[:-1], key=lambda line: (line.split(":")[0], int(line.split(":")[1])))
    assert "src/meridian/drivers.py:15: [dead] meridian.drivers.NginxDriver.forgotten" in lines
    assert main(["--root", str(tree), "--strict"]) == 1


def test_github_actions_annotations(
    tree: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    main(["--root", str(tree)])
    out = capsys.readouterr().out
    assert "::warning file=src/meridian/drivers.py,line=31,title=dead code::[dead] meridian.drivers.dead_helper" in out


def test_clean_tree_passes_strict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(tmp_path, "src/meridian/__init__.py", "")
    _write(tmp_path, "src/meridian/__main__.py", "print('hi')\n")
    assert main(["--root", str(tmp_path), "--strict"]) == 0
    assert capsys.readouterr().out.strip() == "dead-code: 0 dead, 0 test-only, 0 unused-param"


def test_package_reexport_resolves_to_the_defining_module(tree: Path) -> None:
    reexport = 'from meridian.pkg.impl import shown, spare\n\n__all__ = ["shown", "spare"]\n'
    _write(tree, "src/meridian/pkg/__init__.py", reexport)
    _write(tree, "src/meridian/pkg/impl.py", "def shown() -> None:\n    pass\n\n\ndef spare() -> None:\n    pass\n")
    _write(tree, "src/meridian/cli.py", CLI + "\nfrom meridian.pkg import shown\nshown()\n")
    _write(tree, "tests/test_pkg.py", "from meridian.pkg import spare\n\nspare()\n")
    found = _by_category(tree)
    assert "meridian.pkg.impl.shown" not in found["dead"] | found["test-only"]
    assert "meridian.pkg.impl.spare" in found["test-only"]
