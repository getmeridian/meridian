"""Conditionally bundle the built Studio page and the node agent builds in Python distributions."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_DESTINATIONS = {
    "wheel": "meridian/studio_assets",
    "sdist": "src/meridian/studio_assets",
}

# Site-absolute references ("/x", never "//host") in each file type Studio ships.
_HTML_REF = re.compile(r"""\b(?:src|href)\s*=\s*["'](/(?!/)[^"'#?]*)""")
_CSS_REF = re.compile(r"""url\(\s*["']?(/(?!/)[^"')#?]+)""")
_JS_REF = re.compile(r"""(?:\bfrom\s*|\bimport\s*\(?\s*)["']((?:\.{1,2}/|/(?!/))[^"']+)["']""")


# `make agent` writes each static musl build with its sha256 and version beside it in agent/dist;
# the package reads them as meridian/agent_dist (meridian.control_artifacts.PACKAGED_AGENT_DIR).
_AGENT_DESTINATIONS = {
    "wheel": "meridian/agent_dist",
    "sdist": "src/meridian/agent_dist",
}
AGENT_BUILDS = ("netagent-x86_64-unknown-linux-musl", "netagent-aarch64-unknown-linux-musl")
AGENT_SIDECARS = (".sha256", ".version")


def agent_build_files(dist: Path) -> list[str]:
    """The agent builds present in `dist` (each with its sidecars), so a source build without Rust still works."""
    return [
        name + suffix
        for name in AGENT_BUILDS
        if (dist / name).is_file()
        for suffix in ("", *AGENT_SIDECARS)
        if (dist / (name + suffix)).is_file()
    ]


class StudioAssetError(ValueError):
    """The built Studio page references a file the wheel would not contain."""


def studio_asset_closure(dist: Path) -> list[str]:
    """Return the dist-relative files the Studio page needs, and nothing else.

    Starts from `studio/` and follows HTML `src`/`href`, CSS `url()`, and JS
    imports. The website is ~16 MB; Studio is a fraction of it, and the Engine
    serves only what is packaged.
    """
    root = dist.resolve()
    pending = sorted(path for path in (root / "studio").rglob("*") if path.is_file())
    seen: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        for target in _references(root, path):
            if target not in seen:
                pending.append(target)
    licenses = root / "fonts" / "licenses"
    if licenses.is_dir() and any(path.is_relative_to(root / "fonts") for path in seen):
        seen.update(path for path in licenses.iterdir() if path.is_file())  # the OFL travels with its fonts
    return sorted(path.relative_to(root).as_posix() for path in seen)


def _references(root: Path, path: Path) -> list[Path]:
    suffix = path.suffix
    if suffix == ".html":
        refs = _HTML_REF.findall(path.read_text(encoding="utf-8"))
    elif suffix == ".css":
        refs = _CSS_REF.findall(path.read_text(encoding="utf-8"))
    elif suffix in {".js", ".mjs"}:
        refs = _JS_REF.findall(path.read_text(encoding="utf-8"))
    else:
        return []
    targets = []
    for ref in refs:
        if ref.startswith("/"):
            relative = PurePosixPath(ref.lstrip("/"))
            if ref.endswith("/"):
                relative = relative / "index.html"
            target = (root / relative).resolve()
        else:
            target = (path.parent / ref).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise StudioAssetError(
                f"{path.relative_to(root).as_posix()} references {ref}, which is not a file in the built site; "
                "link other site pages with an absolute https:// URL, because the Engine ships only Studio's files"
            )
        targets.append(target)
    return targets


class CustomBuildHook(BuildHookInterface):
    """Include Studio when built, without breaking clean source installs."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        del version
        destination = _DESTINATIONS.get(self.target_name)
        if destination is None:
            return

        force_include = build_data.setdefault("force_include", {})
        agent_destination = _AGENT_DESTINATIONS[self.target_name]
        for relative in agent_build_files(Path(self.root, "agent", "dist")):
            force_include[str(Path(self.root, "agent", "dist", relative))] = f"{agent_destination}/{relative}"

        studio_dist = Path(self.root, "website", "dist")
        if not (studio_dist / "studio" / "index.html").is_file():
            return

        for relative in studio_asset_closure(studio_dist):
            force_include[str(studio_dist / relative)] = f"{destination}/{relative}"
