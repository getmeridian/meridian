from __future__ import annotations

import tomllib
from pathlib import Path

from meridian.engine import assets as assets_module
from meridian.engine.assets import resolve_studio_assets


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


def test_hatch_build_includes_studio_dist_in_wheel_and_sdist() -> None:
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    targets = config["tool"]["hatch"]["build"]["targets"]
    assert targets["wheel"]["force-include"]["website/dist"] == "meridian/studio_assets"
    assert targets["sdist"]["force-include"]["website/dist"] == "src/meridian/studio_assets"
