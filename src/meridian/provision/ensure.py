"""Small idempotent provisioning helpers.

These are Meridian-native equivalents of pyinfra-style semantic operations:
thin wrappers around ``ServerConnection`` that keep check/act/error handling
consistent while leaving project-specific policy in the calling step.
"""

from __future__ import annotations

import re
import shlex
import time
from dataclasses import dataclass
from typing import TypeVar

from meridian.facts import ServerFacts
from meridian.ssh import CommandResult, ServerConnection

_T = TypeVar("_T")


def resolve_ctx(val: _T | None, fallback: _T) -> _T:
    """Resolve a constructor value with context fallback.

    None = "not provided by caller, use context". Explicit values
    (including falsy ones like 0 or "") are respected as-is.
    """
    return val if val is not None else fallback


@dataclass(frozen=True)
class EnsureResult:
    changed: bool = False
    ok: bool = True
    detail: str = ""
    result: CommandResult | None = None


# A fresh server often runs unattended-upgrades or cloud-init's apt for minutes after boot.
APT_LOCK_WAIT_SECONDS = 300
APT_LOCK_RETRY_SECONDS = 5
_APT_LOCK_MARKERS = ("Could not get lock", "Unable to acquire the dpkg frontend lock", "Unable to lock directory")
_APT_LOCK_HOLDER = re.compile(r"held by process (\d+) \(([^)]+)\)")


def apt_lock_held(result: CommandResult) -> bool:
    output = f"{result.stdout}\n{result.stderr}"
    return any(marker in output for marker in _APT_LOCK_MARKERS)


def run_apt(
    conn: ServerConnection,
    command: str,
    *,
    timeout: int,
    operation_name: str = "",
    wait: float = APT_LOCK_WAIT_SECONDS,
) -> CommandResult:
    """Run an apt command, waiting up to `wait` seconds while another package manager holds the lock.

    Lock refusals are retried; any other failure returns at once. Never hide apt's stderr from
    this helper (`2>/dev/null`), or a lock looks like an ordinary failure.
    """
    deadline = time.monotonic() + wait
    while True:
        result = conn.run(
            command,
            timeout=timeout,
            env={"DEBIAN_FRONTEND": "noninteractive"},
            operation_name=operation_name,
        )
        if result.returncode == 0 or not apt_lock_held(result) or time.monotonic() >= deadline:
            return result
        time.sleep(APT_LOCK_RETRY_SECONDS)


def apt_failure(result: CommandResult) -> str:
    """apt's error for the user, saying what to do when another package manager kept the lock."""
    output = (result.stderr or result.stdout).strip()
    if not apt_lock_held(result):
        return output[:300]
    holder = _APT_LOCK_HOLDER.search(output)
    who = f"{holder[2]} (process {holder[1]})" if holder else "another package manager"
    minutes = round(APT_LOCK_WAIT_SECONDS / 60)
    return (
        f"{who} kept the package lock for {minutes} minutes. A fresh server often installs updates "
        "for a while after boot; wait until it finishes, then run the same command again."
    )


def ensure_packages(
    conn: ServerConnection,
    packages: list[str],
    *,
    update: bool = True,
    timeout: int = 300,
) -> EnsureResult:
    """Ensure apt packages are installed."""
    installed = ServerFacts(conn).installed_packages(packages)
    missing = [p for p in packages if p not in installed]
    if not missing:
        return EnsureResult(changed=False, detail="all packages present")

    if update:
        update_result = run_apt(conn, "apt-get update -qq", timeout=180, operation_name="apt update")
        if update_result.returncode != 0:
            return EnsureResult(ok=False, detail=apt_failure(update_result), result=update_result)

    install = run_apt(
        conn,
        "apt-get install -y -qq " + " ".join(shlex.quote(p) for p in missing),
        timeout=timeout,
        operation_name="apt install",
    )
    if install.returncode != 0:
        return EnsureResult(ok=False, detail=apt_failure(install), result=install)
    return EnsureResult(changed=True, detail=f"installed {len(missing)} packages", result=install)


def ensure_file_content(
    conn: ServerConnection,
    path: str,
    content: str,
    *,
    mode: str | int = "644",
    owner: str | None = None,
    sensitive: bool = False,
    create_parent: bool = False,
    timeout: int = 30,
) -> EnsureResult:
    """Ensure a remote text file has exact content."""
    current = conn.get_text(path, timeout=timeout)
    if current.returncode == 0 and current.stdout == content:
        return EnsureResult(changed=False, detail="already configured", result=current)

    write = conn.put_text(
        path,
        content,
        mode=mode,
        owner=owner,
        atomic=True,
        create_parent=create_parent,
        sensitive=sensitive,
        timeout=timeout,
        operation_name=f"write {path}",
    )
    if write.returncode != 0:
        return EnsureResult(ok=False, detail=write.stderr.strip()[:200], result=write)
    return EnsureResult(changed=True, result=write)


def ensure_service_running(
    conn: ServerConnection,
    service: str,
    *,
    restart: bool = False,
    enable: bool = True,
    timeout: int = 30,
) -> EnsureResult:
    """Ensure a systemd service is enabled and running."""
    q_service = shlex.quote(service)
    active = conn.run(f"systemctl is-active {q_service}", timeout=15, operation_name=f"check {service}")
    running = active.returncode == 0 and active.stdout.strip() == "active"
    changed = False

    if enable:
        enabled = conn.run(f"systemctl is-enabled {q_service}", timeout=15, operation_name=f"check enabled {service}")
        if enabled.returncode != 0 or enabled.stdout.strip() not in {"enabled", "enabled-runtime"}:
            enable_result = conn.run(f"systemctl enable {q_service}", timeout=15, operation_name=f"enable {service}")
            if enable_result.returncode != 0:
                return EnsureResult(ok=False, detail=enable_result.stderr.strip()[:200], result=enable_result)
            changed = True

    if running and not restart:
        return EnsureResult(
            changed=changed,
            detail="enabled" if changed else "already running",
            result=active,
        )

    action = "restart" if restart else "start"
    started = conn.run(f"systemctl {action} {q_service}", timeout=timeout, operation_name=f"{action} {service}")
    if started.returncode != 0:
        return EnsureResult(ok=False, detail=started.stderr.strip()[:200], result=started)
    return EnsureResult(changed=True, detail="enabled and started" if enable else "started", result=started)


def ensure_ufw_rule(conn: ServerConnection, rule: str, *, timeout: int = 15) -> EnsureResult:
    """Apply a UFW allow/delete rule and infer changed status from output."""
    result = conn.run(f"ufw {rule}", timeout=timeout, operation_name=f"ufw {rule}")
    if result.returncode != 0:
        return EnsureResult(ok=False, detail=result.stderr.strip()[:200], result=result)
    return EnsureResult(changed="Skipping" not in result.stdout, result=result)


def ufw_rule_present(output: str, rule: str) -> bool:
    """Match a rule against UFW's shell-normalized ``show added`` output."""
    expected = shlex.split(rule)
    for line in output.splitlines():
        try:
            actual = shlex.split(line)
        except ValueError:
            continue
        if actual[:1] == ["ufw"]:
            actual = actual[1:]
        if actual == expected:
            return True
    return False
