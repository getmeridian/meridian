from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest

from meridian.engine import assets as assets_module
from meridian.engine.assets import resolve_studio_assets
from scripts.hatch_build import CustomBuildHook
from scripts.verify_package_assets import verify_wheel_assets


def _studio_dist(root: Path) -> Path:
    dist = root / "dist"
    (dist / "studio").mkdir(parents=True)
    (dist / "studio" / "index.html").write_text("<!doctype html><title>Studio</title>", encoding="utf-8")
    return dist


def test_resolve_studio_assets_prefers_explicit_path(tmp_path: Path) -> None:
    dist = _studio_dist(tmp_path)

    assert resolve_studio_assets(str(dist)) == dist.resolve()


def test_resolve_studio_assets_uses_packaged_resource(monkeypatch, tmp_path: Path) -> None:
    dist = tmp_path / "studio_assets"
    (dist / "studio").mkdir(parents=True)
    (dist / "studio" / "index.html").write_text("<!doctype html><title>Studio</title>", encoding="utf-8")

    monkeypatch.setattr(assets_module, "_PACKAGED_ASSETS", None)
    monkeypatch.setattr(assets_module, "_PACKAGED_ASSETS_CONTEXT", None)
    monkeypatch.setattr(assets_module.resources, "files", lambda _package: tmp_path)

    assert resolve_studio_assets() == dist.resolve()


def _build_hook(root: Path, target_name: str) -> CustomBuildHook:
    return CustomBuildHook(
        str(root),
        {},
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        str(root / "build"),
        target_name,
    )


def test_hatch_build_uses_conditional_studio_hook() -> None:
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    hook = config["tool"]["hatch"]["build"]["hooks"]["custom"]
    assert hook["path"] == "scripts/hatch_build.py"


def test_hatch_build_allows_missing_studio_dist(tmp_path: Path) -> None:
    build_data: dict[str, Any] = {"force_include": {}}

    _build_hook(tmp_path, "wheel").initialize("standard", build_data)

    assert build_data["force_include"] == {}


@pytest.mark.parametrize(
    ("target_name", "destination"),
    [
        ("wheel", "meridian/studio_assets"),
        ("sdist", "src/meridian/studio_assets"),
    ],
)
def test_hatch_build_includes_complete_studio_dist(
    tmp_path: Path,
    target_name: str,
    destination: str,
) -> None:
    dist = _studio_dist(tmp_path / "website")
    build_data: dict[str, Any] = {"force_include": {}}

    _build_hook(tmp_path, target_name).initialize("standard", build_data)

    assert build_data["force_include"] == {str(dist): destination}


def test_verify_wheel_assets_accepts_packaged_studio(tmp_path: Path) -> None:
    wheel = tmp_path / "meridian.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr("meridian/studio_assets/studio/index.html", "<!doctype html>")

    verify_wheel_assets(wheel)


def test_verify_wheel_assets_rejects_missing_studio(tmp_path: Path) -> None:
    wheel = tmp_path / "meridian.whl"
    with ZipFile(wheel, "w"):
        pass

    with pytest.raises(ValueError, match="missing Studio assets"):
        verify_wheel_assets(wheel)
