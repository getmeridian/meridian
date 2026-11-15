"""Bundle the node agent builds and the control service's dependency pins in Python distributions."""

from __future__ import annotations

import shutil
import tempfile
import tomllib
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

# The versions CI tested, read from uv.lock, so the control server installs the same ones
# (meridian.control_artifacts.service_constraints). Generated, so gitignored and never committed:
# a lock update can't leave a stale copy behind. The wheel built from the sdist keeps the sdist's copy.
CONSTRAINTS_NAME = "control-constraints.txt"
_CONSTRAINTS_DESTINATIONS = {
    "wheel": f"meridian/data/{CONSTRAINTS_NAME}",
    "sdist": f"src/meridian/data/{CONSTRAINTS_NAME}",
}
PROJECT = "meridian-vpn"


def agent_build_files(dist: Path) -> list[str]:
    """The agent builds present in `dist` (each with its sidecars), so a source build without Rust still works."""
    return [
        name + suffix
        for name in AGENT_BUILDS
        if (dist / name).is_file()
        for suffix in ("", *AGENT_SIDECARS)
        if (dist / (name + suffix)).is_file()
    ]


def lock_constraints(lock: Path) -> str:
    """Exact pins for every package the project's runtime dependencies pull in, from a uv lockfile.

    Development groups and unrequested extras are left out. Markers are ignored: a pin only
    constrains a package the install would take anyway, so a superset is harmless.
    """
    packages = {package["name"]: package for package in tomllib.loads(lock.read_text(encoding="utf-8"))["package"]}
    pending = list(packages[PROJECT].get("dependencies", []))
    needed: set[str] = set()
    while pending:
        dependency = pending.pop()
        package = packages[dependency["name"]]
        for extra in dependency.get("extra", []):
            pending.extend(package.get("optional-dependencies", {}).get(extra, []))
        if package["name"] not in needed:
            needed.add(package["name"])
            pending.extend(package.get("dependencies", []))
    pins = "".join(f"{name}=={packages[name]['version']}\n" for name in sorted(needed))
    return f"# The versions {PROJECT} was tested with (uv.lock); the control server installs these.\n{pins}"


class CustomBuildHook(BuildHookInterface):
    """Include the agent builds and dependency pins when present, without breaking clean source installs."""

    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        agent_destination = _AGENT_DESTINATIONS.get(self.target_name)
        if agent_destination is None:
            return

        force_include = build_data.setdefault("force_include", {})
        for relative in agent_build_files(Path(self.root, "agent", "dist")):
            force_include[str(Path(self.root, "agent", "dist", relative))] = f"{agent_destination}/{relative}"

        lock = Path(self.root, "uv.lock")
        if version != "editable" and lock.is_file():
            self._generated = Path(tempfile.mkdtemp(prefix="meridian-build-"))
            constraints = self._generated / CONSTRAINTS_NAME
            constraints.write_text(lock_constraints(lock), encoding="utf-8")
            force_include[str(constraints)] = _CONSTRAINTS_DESTINATIONS[self.target_name]

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        del version, build_data, artifact_path
        generated = getattr(self, "_generated", None)
        if generated is not None:
            shutil.rmtree(generated, ignore_errors=True)
