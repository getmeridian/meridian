"""Conditionally bundle generated Studio assets in Python distributions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_DESTINATIONS = {
    "wheel": "meridian/studio_assets",
    "sdist": "src/meridian/studio_assets",
}


class CustomBuildHook(BuildHookInterface):
    """Include Studio when built, without breaking clean source installs."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        del version
        destination = _DESTINATIONS.get(self.target_name)
        if destination is None:
            return

        studio_dist = Path(self.root, "website", "dist")
        if not (studio_dist / "studio" / "index.html").is_file():
            return

        build_data.setdefault("force_include", {})[str(studio_dist)] = destination
