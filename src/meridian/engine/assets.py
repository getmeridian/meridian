"""Studio static asset discovery for the local Engine."""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path
from typing import Any

_PACKAGED_ASSETS: Path | None = None
_PACKAGED_ASSETS_CONTEXT: Any = None

def resolve_studio_assets(explicit: str = "") -> Path | None:
    """Return a directory containing built Studio assets, if one is available."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env_assets = os.environ.get("MERIDIAN_STUDIO_ASSETS", "").strip()
    if env_assets:
        candidates.append(Path(env_assets).expanduser())

    packaged = _packaged_studio_assets()
    if packaged is not None:
        candidates.append(packaged)

    # Editable checkout fallback: `pnpm run build` writes website/dist.
    repo_root = Path(__file__).resolve().parents[3]
    candidates.append(repo_root / "website" / "dist")

    for candidate in candidates:
        if _is_studio_assets_dir(candidate):
            return candidate.resolve()
    return None


def _is_studio_assets_dir(path: Path) -> bool:
    return path.is_dir() and (path / "studio" / "index.html").is_file()


def _packaged_studio_assets() -> Path | None:
    """Return packaged Studio assets when running from an installed wheel."""
    global _PACKAGED_ASSETS, _PACKAGED_ASSETS_CONTEXT

    if _PACKAGED_ASSETS is not None:
        return _PACKAGED_ASSETS if _is_studio_assets_dir(_PACKAGED_ASSETS) else None

    try:
        traversable = resources.files("meridian").joinpath("studio_assets")
    except (FileNotFoundError, ModuleNotFoundError):
        return None

    if isinstance(traversable, Path):
        candidate = traversable
        if _is_studio_assets_dir(candidate):
            _PACKAGED_ASSETS = candidate.resolve()
            return _PACKAGED_ASSETS
        return None

    if not traversable.joinpath("studio", "index.html").is_file():
        return None

    context = resources.as_file(traversable)
    candidate = Path(context.__enter__())
    if _is_studio_assets_dir(candidate):
        _PACKAGED_ASSETS_CONTEXT = context
        _PACKAGED_ASSETS = candidate
        return _PACKAGED_ASSETS
    context.__exit__(None, None, None)
    return None
