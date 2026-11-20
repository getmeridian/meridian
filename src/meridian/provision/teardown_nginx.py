"""Prove that a teardown inspection describes the nginx systemd will stop.

This module also runs as standalone stdlib code on Debian/Ubuntu targets. Unit
environment, process titles and systemd property values must never enter output.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import subprocess
from pathlib import Path

_ROOT = Path("/")
_BINARY = "/usr/sbin/nginx"
_CONFIG = "/etc/nginx/nginx.conf"
_VENDOR = ("lib/systemd/system/nginx.service", "usr/lib/systemd/system/nginx.service")
_DEFAULTS = "/etc/default/nginx"
_RESTART = "/etc/systemd/system/nginx.service.d/restart.conf"
_ERROR = "Cannot verify the packaged nginx service, its launch configuration and running process. Keep the host intact."
_FIELDS = (
    "LoadState",
    "FragmentPath",
    "ExecStart",
    "Environment",
    "EnvironmentFiles",
    "PassEnvironment",
    "UnsetEnvironment",
    "DropInPaths",
    "MainPID",
    "ActiveState",
    "NeedDaemonReload",
)


def _require(ok: object) -> None:
    if not ok:
        raise ValueError(_ERROR)


def _output(*args: str) -> str | None:
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10, check=False)
    return result.stdout.decode() if result.returncode == 0 else None


def _path(absolute: str) -> Path:
    return _ROOT / absolute.lstrip("/")


def _properties() -> dict[str, str]:
    data = _output("systemctl", "show", "nginx.service", "--all", "--property=" + ",".join(_FIELDS))
    props: dict[str, str] = {}
    for line in (data or "").splitlines():
        key, equal, value = line.partition("=")
        _require(equal and key in _FIELDS and key not in props)
        props[key] = value
    _require(set(props) == set(_FIELDS))
    return props


def _packaged_files(fragment: str) -> None:
    binary, unit = _path(_BINARY), _path(fragment)
    _require(fragment in ("/lib/systemd/system/nginx.service", "/usr/lib/systemd/system/nginx.service"))
    _require(binary.resolve(strict=True) == binary)
    real_unit = unit.resolve(strict=True)
    _require(real_unit in {_ROOT / path for path in _VENDOR})
    # dpkg records the old /lib name on pre-usrmerge distributions even when
    # systemd reports /usr/lib. Accept only aliases that reach this very file.
    paths = {binary: [_BINARY.lstrip("/")], real_unit: [key for key in _VENDOR if (_ROOT / key).resolve() == real_unit]}
    sums: dict[str, set[str]] = {}
    for package in ("nginx", "nginx-common"):
        if (_output("dpkg-query", "-W", "-f=${Status}", package) or "").strip() != "install ok installed":
            continue
        manifest = _output("dpkg-query", "--control-show", package, "md5sums")
        for line in (manifest or "").splitlines():
            fields = line.split(maxsplit=1)
            if len(fields) == 2 and fields[1] in {_BINARY.lstrip("/"), *_VENDOR}:
                sums.setdefault(fields[1], set()).add(fields[0])
    for path, names in paths.items():
        _require(path.is_file() and path.stat().st_size <= 64 * 1024 * 1024)
        digest = hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()
        _require(any(digest in sums.get(name, set()) for name in names))


def _environment(props: dict[str, str]) -> None:
    _require(not props["PassEnvironment"] and not props["UnsetEnvironment"])
    _require(shlex.split(props["Environment"]) in ([], ["CONFFILE=" + _CONFIG]))
    envfiles = props["EnvironmentFiles"]
    if envfiles:
        _require(envfiles == _DEFAULTS + " (ignore_errors=yes)")
        path = _path(_DEFAULTS)
        if os.path.lexists(path):
            _require(path.is_file() and path.resolve() == path and path.stat().st_size <= 65536)
            for line in path.read_text().splitlines():
                if line.strip() and not line.lstrip().startswith("#"):
                    _require(shlex.split(line, comments=True) in (["DAEMON_ARGS="], ["CONFFILE=" + _CONFIG]))
    if props["DropInPaths"]:
        _require(props["DropInPaths"] == _RESTART)
        dropin = _path(_RESTART)
        _require(dropin.resolve() == dropin and dropin.read_bytes() == b"[Service]\nRestart=on-failure\nRestartSec=5\n")


def _arguments(exec_start: str, props: dict[str, str]) -> str:
    # systemctl's scalar rendering has a fixed argv prefix, then status fields.
    # Compare the exact invocation; extracting just -c would overlook extra -g/-p.
    prefix = "{ path=" + _BINARY + " ; argv[]="
    _require(exec_start.startswith(prefix) and exec_start.endswith(" }") and exec_start.count("argv[]=") == 1)
    args, separator, _state = exec_start[len(prefix) :].partition(" ; ignore_errors=")
    _require(separator and " ; " not in args)
    if args == _BINARY + " -c ${CONFFILE}":
        _require(shlex.split(props["Environment"]) == ["CONFFILE=" + _CONFIG])
        args = _BINARY + " -c " + _CONFIG
    _require(args in (_BINARY, _BINARY + " -c " + _CONFIG, _BINARY + " -g daemon on; master_process on;"))
    return args


def verify_nginx_runtime() -> None:
    """Refuse an unknown unit, environment, binary or active master without leaking their values."""
    try:
        props = _properties()
        _require(props["LoadState"] == "loaded" and props["NeedDaemonReload"] == "no")
        _packaged_files(props["FragmentPath"])
        _environment(props)
        launch = _arguments(props["ExecStart"], props)
        state, pid = props["ActiveState"], props["MainPID"]
        if state in ("inactive", "failed"):
            _require(pid == "0")
            return
        _require(state == "active" and pid.isdecimal() and int(pid) > 1)
        proc = _path("/proc/" + pid)
        _require((proc / "exe").samefile(_path(_BINARY)))
        _require((proc / "root" / _CONFIG.lstrip("/")).samefile(_path(_CONFIG)))
        # A running master may predate today's environment file (or inherited
        # sockets/libraries from a separate launch). Inspect only relevant keys.
        for field in (proc / "environ").read_bytes().split(b"\x00"):
            name, _, value = field.partition(b"=")
            _require(not (name.startswith(b"LD_") or name == b"NGINX") or not value)
            _require(name != b"CONFFILE" or value == _CONFIG.encode())
            _require(name != b"DAEMON_ARGS" or not value)
        title = (proc / "cmdline").read_bytes().rstrip(b"\x00")
        _require(title == ("nginx: master process " + launch).encode())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        raise ValueError(_ERROR) from None
