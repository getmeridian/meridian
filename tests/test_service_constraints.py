"""The control server installs the dependency versions CI tested: pins from uv.lock ride in the wheel."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from packaging.requirements import Requirement

from meridian.control_artifacts import CONSTRAINTS_RESOURCE, wheel_constraints
from scripts import hatch_build
from scripts.hatch_build import CustomBuildHook, lock_constraints

ROOT = Path(__file__).resolve().parents[1]

LOCK = """
version = 1
requires-python = ">=3.11"

[[package]]
name = "meridian-vpn"
source = { editable = "." }
dependencies = [
    { name = "httpx", extra = ["http2"] },
    { name = "typer" },
]

[package.dev-dependencies]
dev = [{ name = "pytest" }]

[[package]]
name = "httpx"
version = "0.27.2"
dependencies = [{ name = "certifi" }]

[package.optional-dependencies]
http2 = [{ name = "h2" }]
brotli = [{ name = "brotli" }]

[[package]]
name = "typer"
version = "0.29.1"
dependencies = [{ name = "colorama", marker = "sys_platform == 'win32'" }]

[[package]]
name = "certifi"
version = "2026.1.1"

[[package]]
name = "h2"
version = "4.3.0"

[[package]]
name = "colorama"
version = "0.4.6"

[[package]]
name = "brotli"
version = "1.2.0"

[[package]]
name = "pytest"
version = "9.0.0"
"""


def _pins(text: str) -> dict[str, str]:
    lines = [line for line in text.splitlines() if line and not line.startswith("#")]
    return {(req := Requirement(line)).name: str(req.specifier) for line in lines}


def test_pins_cover_runtime_dependencies_and_requested_extras_only(tmp_path: Path) -> None:
    lock = tmp_path / "uv.lock"
    lock.write_text(LOCK)

    assert _pins(lock_constraints(lock)) == {
        "certifi": "==2026.1.1",
        "colorama": "==0.4.6",  # markers are ignored: pinning a package nobody installs is harmless
        "h2": "==4.3.0",
        "httpx": "==0.27.2",
        "typer": "==0.29.1",
    }


def test_the_repository_lock_pins_every_declared_dependency_at_its_locked_version() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = {package["name"]: package["version"] for package in lock["package"] if "version" in package}
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]

    pins = _pins(lock_constraints(ROOT / "uv.lock"))

    for requirement in map(Requirement, declared):
        assert pins[requirement.name] == f"=={locked[requirement.name]}"
    assert hatch_build.PROJECT not in pins


def _hook(root: Path, target_name: str) -> CustomBuildHook:
    return CustomBuildHook(str(root), {}, None, None, str(root / "build"), target_name)  # type: ignore[arg-type]


def test_the_build_hook_packages_the_pins_and_cleans_up(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text(LOCK)
    for target_name, destination in [
        ("wheel", f"meridian/{CONSTRAINTS_RESOURCE}"),
        ("sdist", f"src/meridian/{CONSTRAINTS_RESOURCE}"),
    ]:
        hook = _hook(tmp_path, target_name)
        build_data: dict[str, Any] = {"force_include": {}}

        hook.initialize("standard", build_data)

        ((source, packaged),) = build_data["force_include"].items()
        assert packaged == destination
        assert "typer==0.29.1" in Path(source).read_text()
        hook.finalize("standard", build_data, "")
        assert not Path(source).exists()


def test_editable_installs_and_builds_without_a_lock_carry_no_pins(tmp_path: Path) -> None:
    build_data: dict[str, Any] = {"force_include": {}}
    _hook(tmp_path, "wheel").initialize("standard", build_data)  # an sdist's wheel keeps the sdist's copy
    assert build_data["force_include"] == {}

    (tmp_path / "uv.lock").write_text(LOCK)
    _hook(tmp_path, "wheel").initialize("editable", build_data)
    assert build_data["force_include"] == {}


def test_a_checkout_wheel_hands_its_pins_to_the_install(tmp_path: Path) -> None:
    wheel = tmp_path / "meridian_vpn-4.0.0-py3-none-any.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr(f"meridian/{CONSTRAINTS_RESOURCE}", "typer==0.29.1\n")
    bare = tmp_path / "bare.whl"
    with ZipFile(bare, "w") as archive:
        archive.writestr("meridian/__init__.py", "")

    assert wheel_constraints(wheel) == "typer==0.29.1\n"
    assert wheel_constraints(bare) == ""
    assert wheel_constraints(tmp_path / "missing.whl") == ""
