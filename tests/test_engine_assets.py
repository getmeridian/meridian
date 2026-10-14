from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest

from meridian.engine import assets as assets_module
from meridian.engine.assets import resolve_studio_assets
from scripts import verify_package_assets
from scripts.hatch_build import CustomBuildHook, StudioAssetError, studio_asset_closure
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
    monkeypatch.setattr(assets_module.resources, "files", lambda _package: tmp_path)

    assert resolve_studio_assets() == dist.resolve()


@pytest.mark.skipif(sys.version_info < (3, 12), reason="as_file extracts directories from 3.12")
def test_zipped_packaged_assets_stay_extracted_after_lookup(monkeypatch, tmp_path: Path) -> None:
    import zipfile

    archive = tmp_path / "meridian.zip"
    with ZipFile(archive, "w") as bundle:
        bundle.writestr("studio_assets/studio/index.html", "<!doctype html><title>Studio</title>")

    monkeypatch.setattr(assets_module, "_PACKAGED_ASSETS", None)
    monkeypatch.setattr(assets_module.resources, "files", lambda _package: zipfile.Path(archive))

    found = resolve_studio_assets()

    assert found is not None
    assert not found.is_relative_to(Path(assets_module.__file__).resolve().parents[3])  # not the checkout's dist
    assert (found / "studio" / "index.html").is_file()


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
def test_hatch_build_includes_only_the_studio_page_closure(
    tmp_path: Path,
    target_name: str,
    destination: str,
) -> None:
    dist = _site_dist(tmp_path / "website")
    build_data: dict[str, Any] = {"force_include": {}}

    _build_hook(tmp_path, target_name).initialize("standard", build_data)

    assert build_data["force_include"] == {
        str(dist / relative): f"{destination}/{relative}" for relative in _STUDIO_CLOSURE
    }


_STUDIO_CLOSURE = [
    "_astro/chunk.js",
    "_astro/studio.css",
    "_astro/studio.js",
    "fonts/body.woff2",
    "fonts/licenses/Body-OFL.txt",
    "img/favicon-32.png",
    "studio/index.html",
    "studio/theme.js",
]


def _site_dist(root: Path) -> Path:
    """A miniature website build: the Studio page plus files only other pages use."""
    dist = root / "dist"
    files = {
        "studio/index.html": (
            '<link rel="stylesheet" href="/_astro/studio.css"><link rel="icon" href="/img/favicon-32.png">'
            '<script src="/studio/theme.js"></script><script type="module" src="/_astro/studio.js"></script>'
            '<a href="https://getmeridian.org/docs/">Docs</a><a href="#main">Skip</a>'
        ),
        "studio/theme.js": "document.documentElement.dataset.theme = 'light';",
        "_astro/studio.css": "@font-face { src: url('/fonts/body.woff2') format('woff2'); }",
        "_astro/studio.js": 'import { x } from "./chunk.js"; x();',
        "_astro/chunk.js": "export const x = () => 1;",
        "fonts/body.woff2": "font",
        "fonts/heading.woff2": "unused font",
        "fonts/licenses/Body-OFL.txt": "SIL Open Font License",
        "img/favicon-32.png": "png",
        "img/og.png": "unused image",
        "_astro/landing.js": "unused",
        "index.html": '<script src="/_astro/landing.js"></script>',
        "en/getting-started/index.html": "docs",
        "pagefind/pagefind.js": "search index",
    }
    for relative, text in files.items():
        (dist / relative).parent.mkdir(parents=True, exist_ok=True)
        (dist / relative).write_text(text, encoding="utf-8")
    return dist


def test_studio_closure_refuses_a_reference_the_wheel_cannot_serve(tmp_path: Path) -> None:
    dist = _studio_dist(tmp_path)
    (dist / "studio" / "index.html").write_text('<a href="/docs/">Docs</a>', encoding="utf-8")

    with pytest.raises(StudioAssetError, match="absolute https:// URL"):
        studio_asset_closure(dist)


def test_verify_script_names_the_roots_the_engine_serves() -> None:
    assert verify_package_assets.STUDIO_ASSET_ROOTS == assets_module.STUDIO_ASSET_ROOTS


def test_verify_wheel_assets_accepts_packaged_studio(tmp_path: Path) -> None:
    wheel = tmp_path / "meridian.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr("meridian/studio_assets/studio/index.html", "<!doctype html>")
        archive.writestr("meridian/data/agents.md", "# Meridian for AI agents")

    verify_wheel_assets(wheel)


def test_verify_wheel_assets_rejects_website_files_studio_never_serves(tmp_path: Path) -> None:
    wheel = tmp_path / "meridian.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr("meridian/studio_assets/studio/index.html", "<!doctype html>")
        archive.writestr("meridian/studio_assets/en/getting-started/index.html", "<!doctype html>")
        archive.writestr("meridian/data/agents.md", "# Meridian for AI agents")

    with pytest.raises(ValueError, match="never serves: meridian/studio_assets/en/getting-started/index.html"):
        verify_wheel_assets(wheel)


def test_verify_wheel_assets_rejects_missing_studio(tmp_path: Path) -> None:
    wheel = tmp_path / "meridian.whl"
    with ZipFile(wheel, "w"):
        pass

    with pytest.raises(ValueError, match="missing packaged assets"):
        verify_wheel_assets(wheel)
