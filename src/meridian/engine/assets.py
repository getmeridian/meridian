"""Studio static asset discovery for the local Engine."""

from __future__ import annotations

import os
from pathlib import Path


def resolve_studio_assets(explicit: str = "") -> Path | None:
    """Return a directory containing built Studio assets, if one is available."""
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env_assets = os.environ.get("MERIDIAN_STUDIO_ASSETS", "").strip()
    if env_assets:
        candidates.append(Path(env_assets).expanduser())

    # Editable checkout fallback: `pnpm run build` writes website/dist.
    repo_root = Path(__file__).resolve().parents[3]
    candidates.append(repo_root / "website" / "dist")

    for candidate in candidates:
        if _is_studio_assets_dir(candidate):
            return candidate.resolve()
    return None


def _is_studio_assets_dir(path: Path) -> bool:
    return path.is_dir() and (path / "studio" / "index.html").is_file()
