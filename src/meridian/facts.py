"""Typed server facts gathered over ``ServerConnection``.

This borrows pyinfra's useful idea (facts are first-class data) without
adopting pyinfra's execution engine. Facts are cached per ``ServerFacts``
instance only; callers create a fresh instance after mutating volatile state.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from meridian.core.execution import ServerConnection


@dataclass(frozen=True)
class OsRelease:
    id: str = "ubuntu"
    version_codename: str = "jammy"
    pretty_name: str = ""


@dataclass(frozen=True)
class DockerState:
    installed: bool = False
    compose_available: bool = False
    has_running_containers: bool = False
    version: str = ""


@dataclass(frozen=True)
class UfwState:
    installed: bool = False
    active: bool = False
    raw_status: str = ""


@dataclass(frozen=True)
class ContainerState:
    name: str
    running: bool = False
    status: str = ""


def parse_ssh_ports(output: str) -> list[int]:
    """Parse one or more SSH ports from command output."""
    ports: list[int] = []
    seen: set[int] = set()

    for line in output.splitlines():
        for token in line.replace(",", " ").split():
            if not token.isdigit():
                continue
            port = int(token)
            if not (1 <= port <= 65535) or port in seen:
                continue
            seen.add(port)
            ports.append(port)
            break

    return ports


def _parse_os_release(text: str) -> OsRelease:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        try:
            parsed = shlex.split(value)
            values[key] = parsed[0] if parsed else ""
        except ValueError:
            values[key] = value.strip().strip('"')
    return OsRelease(
        id=values.get("ID", "ubuntu").lower() or "ubuntu",
        version_codename=values.get("VERSION_CODENAME", "jammy") or "jammy",
        pretty_name=values.get("PRETTY_NAME", ""),
    )


@dataclass
class ServerFacts:
    """Small per-target fact cache."""

    conn: ServerConnection
    _cache: dict[str, Any] = field(default_factory=dict)

    def invalidate(self, *names: str) -> None:
        """Clear cached facts by name, or all facts if no names are passed."""
        if not names:
            self._cache.clear()
            return
        for name in names:
            self._cache.pop(name, None)

    def os_release(self) -> OsRelease:
        if "os_release" not in self._cache:
            result = self.conn.run("cat /etc/os-release 2>/dev/null", timeout=15)
            self._cache["os_release"] = _parse_os_release(result.stdout if result.returncode == 0 else "")
        return self._cache["os_release"]

    def arch(self) -> str:
        if "arch" not in self._cache:
            result = self.conn.run("uname -m", timeout=15)
            self._cache["arch"] = result.stdout.strip() if result.returncode == 0 else ""
        return self._cache["arch"]

    def dpkg_arch(self) -> str:
        if "dpkg_arch" not in self._cache:
            result = self.conn.run("dpkg --print-architecture", timeout=15)
            self._cache["dpkg_arch"] = result.stdout.strip() if result.returncode == 0 else "amd64"
        return self._cache["dpkg_arch"]

    def ssh_ports(self) -> list[int]:
        if "ssh_ports" not in self._cache:
            commands = [
                r"""sshd -T 2>/dev/null | awk '$1 == "port" {print $2}'""",
                (
                    r"""grep -hEi '^[[:space:]]*Port[[:space:]]+[0-9]+' """
                    r"""/etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null """
                    r"""| awk '{print $2}'"""
                ),
            ]
            ports: list[int] = []
            for command in commands:
                result = self.conn.run(command, timeout=15)
                if result.returncode != 0:
                    continue
                ports = parse_ssh_ports(result.stdout)
                if ports:
                    break
            self._cache["ssh_ports"] = ports or [22]
        return list(self._cache["ssh_ports"])

    def free_disk_mb(self, path: str = "/") -> int | None:
        key = f"free_disk_mb:{path}"
        if key not in self._cache:
            result = self.conn.run(f"df -BM --output=avail {shlex.quote(path)} | tail -1", timeout=15)
            if result.returncode != 0:
                self._cache[key] = None
            else:
                try:
                    self._cache[key] = int(result.stdout.strip().rstrip("M"))
                except (ValueError, AttributeError):
                    self._cache[key] = None
        return self._cache[key]

    def installed_packages(self, packages: list[str]) -> set[str]:
        if not packages:
            return set()
        q_packages = " ".join(shlex.quote(p) for p in packages)
        result = self.conn.run(f"dpkg-query -W -f='${{Package}}\\n' {q_packages} 2>/dev/null", timeout=15)
        return set(result.stdout.strip().splitlines()) if result.returncode == 0 else set()

    def docker_state(self) -> DockerState:
        if "docker_state" not in self._cache:
            version = self.conn.run("docker --version", timeout=15)
            compose = self.conn.run("docker compose version", timeout=15) if version.returncode == 0 else None
            ps = self.conn.run("docker ps -q", timeout=15) if version.returncode == 0 else None
            self._cache["docker_state"] = DockerState(
                installed=version.returncode == 0,
                compose_available=bool(compose and compose.returncode == 0),
                has_running_containers=bool(ps and ps.returncode == 0 and ps.stdout.strip()),
                version=version.stdout.strip() if version.returncode == 0 else "",
            )
        return self._cache["docker_state"]

    def container_state(self, name: str) -> ContainerState:
        key = f"container_state:{name}"
        if key not in self._cache:
            q_name = shlex.quote(name)
            status = self.conn.run(f"docker inspect -f '{{{{.State.Status}}}}' {q_name} 2>/dev/null", timeout=15)
            running = self.conn.run(f"docker inspect -f '{{{{.State.Running}}}}' {q_name} 2>/dev/null", timeout=15)
            self._cache[key] = ContainerState(
                name=name,
                running=running.returncode == 0 and running.stdout.strip() == "true",
                status=status.stdout.strip() if status.returncode == 0 else "",
            )
        return self._cache[key]

    def ufw_state(self) -> UfwState:
        if "ufw_state" not in self._cache:
            which = self.conn.run("which ufw 2>/dev/null", timeout=15)
            if which.returncode != 0:
                self._cache["ufw_state"] = UfwState()
            else:
                status = self.conn.run("ufw status", timeout=15)
                raw = status.stdout if status.returncode == 0 else ""
                self._cache["ufw_state"] = UfwState(
                    installed=True,
                    active="Status: active" in raw,
                    raw_status=raw,
                )
        return self._cache["ufw_state"]

    def sysctl(self, key: str) -> str:
        cache_key = f"sysctl:{key}"
        if cache_key not in self._cache:
            result = self.conn.run(f"sysctl -n {shlex.quote(key)} 2>/dev/null", timeout=15)
            self._cache[cache_key] = result.stdout.strip() if result.returncode == 0 else ""
        return self._cache[cache_key]
