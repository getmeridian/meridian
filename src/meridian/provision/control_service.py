"""Install Meridian's own control service (`meridian serve`) on the control server.

A sha256-pinned uv installs `meridian-vpn` with its own Python under
`/opt/ctld`, so the service never depends on the distribution's Python (Ubuntu
22.04 ships 3.10). It runs as the `ctld` system user on loopback; nginx's
secret path is its only way in.

The install never downgrades: a server whose recorded package is newer than
this CLI's fails the step (`core/version_skew.py`), since the newer service's
database refuses to open under the older one.
"""

from __future__ import annotations

import json
import shlex
import time
from dataclasses import dataclass

from meridian.config import CONTROL_SERVICE_PYTHON, UV_GITHUB_URL, UV_SHA256, UV_VERSION
from meridian.control.models import ServiceConfig
from meridian.control_artifacts import ServicePackage, package_version
from meridian.core.version_skew import newer_service_hint, service_skew
from meridian.host_runtime import (
    CONTROL_SERVICE_CONFIG,
    CONTROL_SERVICE_DIR,
    CONTROL_SERVICE_NAME,
    CONTROL_SERVICE_STATE_DIR,
    CONTROL_SERVICE_UNIT,
)
from meridian.provision.ensure import ensure_file_content
from meridian.provision.steps import ProvisionContext, StepResult
from meridian.ssh import ServerConnection

UV_BINARY = f"{CONTROL_SERVICE_DIR}/bin/uv"
SERVICE_BINARY = f"{CONTROL_SERVICE_DIR}/bin/meridian"
INSTALLED_MARKER = f"{CONTROL_SERVICE_DIR}/installed"
_UV_ENV = (
    f"UV_PYTHON_INSTALL_DIR={CONTROL_SERVICE_DIR}/python UV_TOOL_DIR={CONTROL_SERVICE_DIR}/tools "
    f"UV_TOOL_BIN_DIR={CONTROL_SERVICE_DIR}/bin UV_CACHE_DIR={CONTROL_SERVICE_DIR}/cache "
    "UV_PYTHON_PREFERENCE=only-managed"
)

# The service's system user and its private state directory; `meridian restore` loads a database into it.
SERVICE_USER_COMMAND = (
    f"id -u {CONTROL_SERVICE_NAME} >/dev/null 2>&1 || useradd --system --home-dir "
    f"{CONTROL_SERVICE_STATE_DIR} --shell /usr/sbin/nologin {CONTROL_SERVICE_NAME}; "
    f"install -d -m 750 -o {CONTROL_SERVICE_NAME} -g {CONTROL_SERVICE_NAME} {CONTROL_SERVICE_STATE_DIR}"
)


@dataclass(frozen=True)
class ControlServiceInstall:
    """What one control-service install writes: the package and the hashed configuration."""

    package: ServicePackage
    config: ServiceConfig


def render_service_unit() -> str:
    return (
        "[Unit]\n"
        "Description=Control service\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        f"User={CONTROL_SERVICE_NAME}\n"
        f"Group={CONTROL_SERVICE_NAME}\n"
        f"ExecStart={SERVICE_BINARY} serve --config {CONTROL_SERVICE_CONFIG}\n"
        "Restart=always\n"
        "RestartSec=3\n"
        "MemoryMax=256M\n"
        "OOMScoreAdjust=-500\n"
        "NoNewPrivileges=yes\n"
        "ProtectSystem=strict\n"
        "ProtectHome=yes\n"
        "PrivateTmp=yes\n"
        f"ReadWritePaths={CONTROL_SERVICE_STATE_DIR}\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


class InstallControlService:
    """Install uv, the pinned service package, its configuration, and its systemd unit."""

    name = "Install control service"

    def __init__(self, install: ControlServiceInstall) -> None:
        self.install = install

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        self._changed = False
        for step in (self._uv, self._user, self._package, self._config, self._unit, self._ready):
            failure = step(conn)
            if failure:
                return StepResult(name=self.name, status="failed", detail=failure)
        return StepResult(
            name=self.name,
            status="changed" if self._changed else "ok",
            detail=f"listening on 127.0.0.1:{self.install.config.listen_port}",
        )

    def _uv(self, conn: ServerConnection) -> str:
        current = conn.run(f"{UV_BINARY} --version 2>/dev/null", timeout=15)
        if current.returncode == 0 and current.stdout.split()[1:2] == [UV_VERSION]:
            return ""
        arch = conn.run("uname -m", timeout=15).stdout.strip()
        digest = UV_SHA256.get(arch)
        if digest is None:
            return f"no pinned uv build for {arch}"
        asset = f"uv-{arch}-unknown-linux-musl"
        url = f"{UV_GITHUB_URL}/{UV_VERSION}/{asset}.tar.gz"
        result = conn.run(
            "set -e; tmp=$(mktemp -d); trap 'rm -rf \"$tmp\"' EXIT; "
            f'curl -fsSL --retry 3 -o "$tmp/uv.tgz" {shlex.quote(url)}; '
            f"echo {shlex.quote(digest)}'  '\"$tmp/uv.tgz\" | sha256sum -c --quiet; "
            f'tar -xzf "$tmp/uv.tgz" -C "$tmp"; mkdir -p {CONTROL_SERVICE_DIR}/bin; '
            f'install -m 755 "$tmp/{asset}/uv" {UV_BINARY}',
            timeout=300,
            operation_name="install uv",
        )
        self._changed = True
        return "" if result.returncode == 0 else f"could not install uv {UV_VERSION}: {result.stderr.strip()[:200]}"

    def _user(self, conn: ServerConnection) -> str:
        result = conn.run(SERVICE_USER_COMMAND, timeout=30)
        return "" if result.returncode == 0 else f"could not create the service user: {result.stderr.strip()[:200]}"

    def _package(self, conn: ServerConnection) -> str:
        package = self.install.package
        read = conn.run(f"if [ -e {INSTALLED_MARKER} ]; then cat {INSTALLED_MARKER}; fi", timeout=15)
        if read.returncode != 0:
            # Unread, it could hide a newer service that `--force` would downgrade.
            detail = (read.stderr or read.stdout).strip()[:200] or f"exit {read.returncode}"
            return f"could not read which service package is installed: {detail}"
        marker = read.stdout.strip()
        if marker == package.identity and conn.run(f"test -x {SERVICE_BINARY}", timeout=15).returncode == 0:
            return ""
        installed = package_version(marker)
        if installed and service_skew(package.version, installed) == "service_newer":
            return (
                f"the control server runs Meridian {installed}, newer than this Meridian ({package.version}). "
                + newer_service_hint(installed)
            )
        spec = package.requirement
        if package.wheel:
            spec = f"{CONTROL_SERVICE_DIR}/wheels/{package.wheel_name}"
            upload = conn.put_bytes(spec, package.wheel, mode="644", create_parent=True, timeout=120)
            if upload.returncode != 0:
                return f"could not upload the service wheel: {upload.stderr.strip()[:200]}"
        result = conn.run(
            f"{_UV_ENV} {UV_BINARY} tool install --force --python {CONTROL_SERVICE_PYTHON} {shlex.quote(spec)}",
            timeout=900,
            operation_name="install control service",
        )
        if result.returncode != 0:
            return f"could not install the service package: {(result.stderr or result.stdout).strip()[-300:]}"
        self._changed = True
        written = conn.put_text(INSTALLED_MARKER, package.identity + "\n", mode="644", timeout=15)
        return "" if written.returncode == 0 else "could not record the installed package"

    def _config(self, conn: ServerConnection) -> str:
        body = json.dumps(self.install.config.model_dump(mode="json"), indent=2) + "\n"
        written = ensure_file_content(
            conn, CONTROL_SERVICE_CONFIG, body, mode="640", owner=f"root:{CONTROL_SERVICE_NAME}", create_parent=True
        )
        self._changed |= written.changed
        return "" if written.ok else f"could not write {CONTROL_SERVICE_CONFIG}: {written.detail}"

    def _unit(self, conn: ServerConnection) -> str:
        path = f"/etc/systemd/system/{CONTROL_SERVICE_UNIT}"
        written = ensure_file_content(conn, path, render_service_unit())
        if not written.ok:
            return f"could not write {path}: {written.detail}"
        self._changed |= written.changed
        active = conn.run(f"systemctl is-active {CONTROL_SERVICE_UNIT}", timeout=15).stdout.strip() == "active"
        if active and not self._changed and not self._outdated(conn, path):
            return ""
        # A new package, config, or unit only takes effect after a restart.
        restart = conn.run(
            f"systemctl daemon-reload && systemctl enable {CONTROL_SERVICE_UNIT} >/dev/null 2>&1 && "
            f"systemctl restart {CONTROL_SERVICE_UNIT}",
            timeout=60,
        )
        self._changed = True
        return (
            "" if restart.returncode == 0 else f"could not start {CONTROL_SERVICE_UNIT}: {restart.stderr.strip()[:200]}"
        )

    def _outdated(self, conn: ServerConnection, unit: str) -> bool:
        """Whether the running service started before its package, config, or unit last changed.

        A run whose reply was lost after writing them never restarted; the retry finds them
        current and must still restart. An unreadable answer restarts too: it costs a second.
        """
        files = " ".join(shlex.quote(path) for path in (INSTALLED_MARKER, CONTROL_SERVICE_CONFIG, unit))
        result = conn.run(
            # systemd's own start time: minimal images have no `ps`.
            f"ts=$(systemctl show -p ExecMainStartTimestamp --value {CONTROL_SERVICE_UNIT}); "
            '[ -n "$ts" ] || exit 0; started=$(date -d "$ts" +%s) || exit 1; '
            f"for f in {files}; do "
            '[ -e "$f" ] && [ "$(stat -c %Y "$f")" -gt "$started" ] && echo outdated; done; exit 0',
            timeout=15,
        )
        return result.returncode != 0 or "outdated" in result.stdout

    def _ready(self, conn: ServerConnection) -> str:
        port = self.install.config.listen_port
        probe = f"curl -s -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{port}/api/health | grep -q 401"
        for _ in range(30):
            if conn.run(probe, timeout=15).returncode == 0:
                return ""
            time.sleep(2)
        logs = conn.run(f"journalctl -u {CONTROL_SERVICE_UNIT} -n 5 --no-pager -o cat", timeout=15)
        return f"{CONTROL_SERVICE_UNIT} did not answer: {logs.stdout.strip()[-300:]}"
