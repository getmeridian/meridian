"""Relay node provisioner — deploys Realm TCP relay via SSH.

A relay node is a lightweight TCP forwarder with no Docker or panel.
It runs Realm to forward port 443 to an exit server, preserving end-to-end
VLESS+Reality encryption between the client and the exit.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field

from meridian.config import (
    REALM_GITHUB_URL,
    REALM_SHA256,
    REALM_VERSION,
    RELAY_BINARY_PATH,
)
from meridian.provision.steps import StepResult
from meridian.ssh import ServerConnection

# Minimal packages needed on a relay node
_REALM_VERSION_RE = re.compile(
    r"^\s*Realm\s+(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)(?=\s|$)",
    re.IGNORECASE,
)


@dataclass
class RelayContext:
    """Configuration for relay node provisioning."""

    relay_ip: str
    exit_ip: str
    exit_port: int = 443
    listen_port: int = 443
    user: str = "root"
    realm_version: str = REALM_VERSION
    # Tarball sha256 per target triple for realm_version; a target without one is refused.
    realm_sha256: Mapping[str, str] = field(default_factory=lambda: dict(REALM_SHA256))
    results: list[StepResult] = field(default_factory=list)

    def __post_init__(self) -> None:
        import ipaddress

        # Validate IP addresses — prevents shell/config injection
        for field_name, value in [("relay_ip", self.relay_ip), ("exit_ip", self.exit_ip)]:
            try:
                ipaddress.ip_address(value)
            except ValueError:
                raise ValueError(f"Invalid IP address for {field_name}: {value!r}") from None

        # Validate port ranges
        for field_name, port in [("exit_port", self.exit_port), ("listen_port", self.listen_port)]:
            if not isinstance(port, int) or not (1 <= port <= 65535):
                raise ValueError(f"Invalid port for {field_name}: {port!r} (must be 1-65535)")


def parse_realm_version(output: str) -> str:
    """Extract Realm's semver without mistaking feature suffixes for it."""
    match = _REALM_VERSION_RE.match(output)
    return match.group(1) if match is not None else ""


# ---------------------------------------------------------------------------
# Relay provisioning steps
# ---------------------------------------------------------------------------


class InstallRealm:
    """Download Realm from GitHub releases and install it under a neutral name (``RELAY_BINARY_PATH``)."""

    name = "Install TCP forwarder"

    def run(self, conn: ServerConnection, ctx: RelayContext) -> StepResult:
        binary = shlex.quote(RELAY_BINARY_PATH)
        # Check if the forwarder is already installed at the right version
        check = conn.run(f"{binary} --version 2>/dev/null", timeout=15)
        if check.returncode == 0:
            installed_version = parse_realm_version(check.stdout)
            if installed_version == ctx.realm_version:
                return StepResult(name=self.name, status="ok", detail=f"v{ctx.realm_version} already installed")

        # Detect architecture
        arch_result = conn.run("uname -m", timeout=15)
        if arch_result.returncode != 0:
            return StepResult(name=self.name, status="failed", detail="cannot detect architecture")

        arch = arch_result.stdout.strip()
        if arch == "x86_64":
            target = "x86_64-unknown-linux-musl"
        elif arch in ("aarch64", "arm64"):
            target = "aarch64-unknown-linux-musl"
        else:
            return StepResult(name=self.name, status="failed", detail=f"unsupported arch: {arch}")

        # Download Realm binary
        url = f"{REALM_GITHUB_URL}/v{ctx.realm_version}/realm-{target}.tar.gz"
        q_url = shlex.quote(url)

        download = conn.run(
            f"curl -fsSL {q_url} -o /tmp/fwd.tar.gz",
            timeout=120,
        )
        if download.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"download failed: {download.stderr.strip()[:200]}",
            )

        # Verify SHA256 checksum (supply chain protection); never install an unpinned tarball.
        expected_hash = ctx.realm_sha256.get(target, "")
        check = conn.run("sha256sum /tmp/fwd.tar.gz | cut -d' ' -f1", timeout=15)
        actual_hash = check.stdout.strip()
        if not expected_hash or actual_hash != expected_hash:
            conn.run("rm -f /tmp/fwd.tar.gz", timeout=15)
            detail = (
                f"checksum mismatch: expected {expected_hash[:16]}..., got {actual_hash[:16]}..."
                if expected_hash
                else f"no checksum pinned for v{ctx.realm_version} on {target}"
            )
            return StepResult(name=self.name, status="failed", detail=detail)

        # Extract into a private directory so the upstream file name never lands in a shared path,
        # and run the new binary there: the running one is replaced only once it starts.
        extract = conn.run('d=$(mktemp -d) && tar xzf /tmp/fwd.tar.gz -C "$d" && echo "$d"', timeout=30)
        stage = extract.stdout.strip().splitlines()[-1] if extract.stdout.strip() else ""
        if extract.returncode != 0 or not stage.startswith("/"):
            conn.run("rm -f /tmp/fwd.tar.gz", timeout=15)
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"extract failed: {extract.stderr.strip()[:200]}",
            )
        q_stage = shlex.quote(stage)
        staged = conn.run(f"{q_stage}/realm --version", timeout=15)
        if staged.returncode != 0 or parse_realm_version(staged.stdout) != ctx.realm_version:
            conn.run(f"rm -rf {q_stage} /tmp/fwd.tar.gz", timeout=15)
            reason = (staged.stderr or staged.stdout).strip()[:200]
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"v{ctx.realm_version} for {target} does not run on this server: {reason}",
            )
        installed = conn.run(
            f"install -m 755 {q_stage}/realm {binary}.new && mv -f {binary}.new {binary} && "
            f"rm -rf {q_stage} /tmp/fwd.tar.gz",
            timeout=30,
        )
        if installed.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"install failed: {installed.stderr.strip()[:200]}",
            )

        verify = conn.run(f"{binary} --version", timeout=15)
        if verify.returncode != 0 or parse_realm_version(verify.stdout) != ctx.realm_version:
            return StepResult(name=self.name, status="failed", detail="forwarder version mismatch after install")

        return StepResult(name=self.name, status="changed", detail=f"installed v{ctx.realm_version}")
