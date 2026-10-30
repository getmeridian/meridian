"""Bundle the node agent builds in Python distributions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

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


class CustomBuildHook(BuildHookInterface):
    """Include the node agent builds when present, without breaking clean source installs."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        del version
        agent_destination = _AGENT_DESTINATIONS.get(self.target_name)
        if agent_destination is None:
            return

        force_include = build_data.setdefault("force_include", {})
        for relative in agent_build_files(Path(self.root, "agent", "dist")):
            force_include[str(Path(self.root, "agent", "dist", relative))] = f"{agent_destination}/{relative}"
