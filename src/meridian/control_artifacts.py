"""What the deployer installs for Meridian's own control plane: the service package and the node agent.

A release installs `meridian-vpn==<this version>` from PyPI on the control
server and uploads the agent builds its wheel carries (`meridian/agent_dist/`,
both musl targets with their sha256 and version files). A development checkout
(an editable install) builds and uploads its own wheel and uses the agent
that `make agent` built, so unreleased code can be tested on a real server.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from functools import cache
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlparse

from meridian import __version__
from meridian.core.errors import LocalStateError

AGENT_TARGETS = {"x86_64": "x86_64-unknown-linux-musl", "aarch64": "aarch64-unknown-linux-musl"}
# Where a built wheel carries the agent builds (scripts/hatch_build.py copies them from agent/dist).
PACKAGED_AGENT_DIR = Path(__file__).resolve().parent / "agent_dist"


@dataclass(frozen=True)
class ServicePackage:
    """`meridian-vpn` for the control server: a PyPI requirement, or a local wheel to upload."""

    requirement: str
    wheel: bytes = b""
    wheel_name: str = ""

    @property
    def identity(self) -> str:
        """What the server records to tell an installed package from a new one."""
        if self.wheel:
            return f"{self.wheel_name}:{hashlib.sha256(self.wheel).hexdigest()}"
        return self.requirement

    @property
    def version(self) -> str:
        return package_version(self.identity)


def package_version(identity: str) -> str:
    """The `meridian-vpn` version a recorded package identity names, or "" when it names none.

    Identities are `meridian-vpn==<version>` (a release) or `<wheel file name>:<sha256>` (a checkout's
    wheel, `meridian_vpn-<version>-<tags>.whl`).
    """
    name, separator, version = identity.partition("==")
    if separator:
        return version.strip() if name.strip() == "meridian-vpn" else ""
    wheel = identity.partition(":")[0]
    parts = wheel.split("-")
    return parts[1] if len(parts) >= 3 and parts[0] == "meridian_vpn" and wheel.endswith(".whl") else ""


@dataclass(frozen=True)
class AgentBinary:
    data: bytes
    sha256: str
    version: str = ""  # From the build's `.version` file; "" when the build has none.


def source_checkout() -> Path | None:
    """The repository an editable install runs from, or None for a regular install."""
    try:
        raw = metadata.distribution("meridian-vpn").read_text("direct_url.json")
    except metadata.PackageNotFoundError:
        return None
    if not raw:
        return None
    info = json.loads(raw)
    if not info.get("dir_info", {}).get("editable"):
        return None
    url = urlparse(info.get("url", ""))
    return Path(unquote(url.path)) if url.scheme == "file" else None


@cache
def service_package() -> ServicePackage:
    checkout = source_checkout()
    if checkout is None:
        return ServicePackage(requirement=f"meridian-vpn=={__version__}")
    with tempfile.TemporaryDirectory(prefix="meridian-wheel-") as out:
        try:
            subprocess.run(
                ["uv", "build", "--wheel", "--out-dir", out, str(checkout)],
                check=True,
                capture_output=True,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise LocalStateError(
                "Could not build this checkout's wheel for the control service.",
                hint="Run `uv build --wheel` in the repository to see the error.",
            ) from exc
        wheels = sorted(Path(out).glob("meridian_vpn-*.whl"))
        if not wheels:
            raise LocalStateError("uv build produced no meridian-vpn wheel.")
        return ServicePackage(requirement="", wheel=wheels[-1].read_bytes(), wheel_name=wheels[-1].name)


def agent_binary(arch: str) -> AgentBinary:
    """The node agent for one machine architecture (`uname -m`), checked against its sha256 file."""
    target = AGENT_TARGETS.get(arch)
    if target is None:
        supported = ", ".join(sorted(AGENT_TARGETS))
        raise LocalStateError(
            f"Meridian's node agent has no build for {arch or 'this architecture'}.",
            hint=f"Use a server with one of: {supported}.",
            category="user",
        )
    path = _agent_path(target)
    if path is None or not path.is_file():
        raise _missing_agent()
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    expected = _sidecar(path, ".sha256")
    # A packaged build always carries its digest; a checkout's or an override's is checked when present.
    if (expected or path.is_relative_to(PACKAGED_AGENT_DIR)) and expected != digest:
        raise LocalStateError(
            f"The node agent build {path.name} does not match its sha256 file.",
            hint="Reinstall Meridian (`meridian update`), or rebuild it with `make agent` in a checkout.",
            category="system",
        )
    return AgentBinary(data=data, sha256=digest, version=_build_version(path))


def require_agent_build() -> None:
    """Refuse before anything is provisioned when this Meridian has no agent build to install at all."""
    if not any((path := _agent_path(target)) is not None and path.is_file() for target in AGENT_TARGETS.values()):
        raise _missing_agent()


def _missing_agent() -> LocalStateError:
    # Released wheels carry both builds; only a source install or a checkout without `make agent` lacks them.
    return LocalStateError(
        "The node agent binary is missing.",
        hint="Install Meridian from PyPI, run `make agent` in a checkout, or point MERIDIAN_AGENT_BINARY at one.",
        category="user",
    )


def agent_version() -> str:
    """The agent version this Meridian installs (every target shares one crate version), or ""."""
    for target in AGENT_TARGETS.values():
        path = _agent_path(target)
        if path is not None and path.is_file():
            return _build_version(path)
    return ""


def _agent_path(target: str) -> Path | None:
    """MERIDIAN_AGENT_BINARY, else the wheel's packaged build, else an editable checkout's `agent/dist`."""
    override = os.environ.get("MERIDIAN_AGENT_BINARY", "")
    if override:
        return Path(override)
    packaged = PACKAGED_AGENT_DIR / f"netagent-{target}"
    if packaged.is_file():
        return packaged
    checkout = source_checkout()
    return checkout / "agent" / "dist" / f"netagent-{target}" if checkout else None


def _build_version(binary: Path) -> str:
    """`make agent` writes the crate version beside each binary as `<binary>.version`."""
    return _sidecar(binary, ".version")


def _sidecar(binary: Path, suffix: str) -> str:
    try:
        return binary.with_name(binary.name + suffix).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
