"""What the deployer installs for Meridian's own control plane: the service package and the node agent.

A release installs `meridian-vpn==<this version>` from PyPI on the control
server and the agent binary its release publishes. A development checkout
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

AGENT_TARGETS = {"x86_64": "x86_64-unknown-linux-musl"}


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


@dataclass(frozen=True)
class AgentBinary:
    data: bytes
    sha256: str


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
    """The node agent for one machine architecture (`uname -m`)."""
    target = AGENT_TARGETS.get(arch)
    if target is None:
        raise LocalStateError(
            f"Meridian's node agent has no build for {arch} yet.",
            hint="Use an x86_64 server for the control-service preview.",
            category="user",
        )
    override = os.environ.get("MERIDIAN_AGENT_BINARY", "")
    checkout = source_checkout()
    path = Path(override) if override else (checkout / "agent" / "dist" / f"netagent-{target}" if checkout else None)
    if path is None or not path.is_file():
        raise LocalStateError(
            "The node agent binary is missing.",
            hint="Build it with `make agent` in the repository, or point MERIDIAN_AGENT_BINARY at one.",
            category="user",
        )
    data = path.read_bytes()
    return AgentBinary(data=data, sha256=hashlib.sha256(data).hexdigest())
