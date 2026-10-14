"""Recent, redacted log tails from the components one fleet server runs (`meridian logs`)."""

from __future__ import annotations

import re
import shlex
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from meridian.core.errors import MeridianError
from meridian.core.execution import ServerConnection
from meridian.core.redaction import redact_diagnostic_text
from meridian.diagnostics.images import control_components, node_components

LogComponent = Literal["node", "panel", "nginx", "relay"]
LOG_COMPONENTS: tuple[LogComponent, ...] = ("node", "panel", "nginx", "relay")
NGINX_ERROR_LOG = "/var/log/nginx/error.log"

# Exit status a tail command uses for "this container, unit tool, or file is not there".
_ABSENT = 10
_JOURNAL_EMPTY = "-- No entries --"
# ANSI escape sequences and other control characters: log lines can carry
# attacker-chosen bytes (a prober's request line), which must not drive the terminal.
_CONTROL_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]|[\x00-\x08\x0b-\x1f\x7f]")


@dataclass(frozen=True)
class LogSource:
    """One log a server keeps: a Docker container, systemd units, or a file."""

    component: LogComponent
    kind: Literal["container", "unit", "file"]
    name: str
    legacy_names: tuple[str, ...] = ()  # pre-rename unit names whose journal still holds history


@dataclass(frozen=True)
class LogTail:
    """The redacted last lines of one source; ``error`` set means the log could not be read."""

    source: LogSource
    lines: tuple[str, ...] = ()
    error: str = ""

    @property
    def available(self) -> bool:
        return not self.error


def log_sources(
    roles: Collection[str],
    *,
    subscription_page: bool,
    relay_units: Sequence[tuple[str, str]] = (),
) -> list[LogSource]:
    """The logs a server holding *roles* keeps, from the same container names deploy and check use.

    *relay_units* pairs each Realm hop's unit with its pre-rename name.
    """
    sources: list[LogSource] = []
    if {"exit", "gateway"} & set(roles):
        sources.extend(LogSource("node", "container", item.container) for item in node_components())
    if "control" in roles:
        sources.extend(
            LogSource("panel", "container", item.container)
            for item in control_components()
            if subscription_page or item.component != "subscription_page"
        )
    if {"control", "exit", "gateway"} & set(roles):
        sources.append(LogSource("nginx", "file", NGINX_ERROR_LOG))
    if "relay" in roles:
        sources.extend(LogSource("relay", "unit", unit, (legacy,)) for unit, legacy in relay_units)
    return sources


def tail_command(source: LogSource, *, lines: int, pattern: str = "") -> str:
    """The shell command printing the last *lines* of *source*, only those containing *pattern* when set.

    The pattern is a case-insensitive fixed string (``grep -a -F -i``), so a user's
    search text never becomes a regular expression or a shell word.
    """
    count = shlex.quote(str(lines))
    name = shlex.quote(source.name)
    # -a: container logs carry raw bytes, and grep prints nothing for a "binary" stream.
    grep = f"grep -a -F -i -e {shlex.quote(pattern)}"
    if source.kind == "container":
        guard = f"docker inspect --type container {name} >/dev/null 2>&1 || exit {_ABSENT}"
        read = (
            f"docker logs {name} 2>&1 | {grep} | tail -n {count}"
            if pattern
            else f"docker logs --tail {count} {name} 2>&1"
        )
    elif source.kind == "file":
        guard = f"test -r {name} || exit {_ABSENT}"
        read = f"{grep} {name} | tail -n {count}" if pattern else f"tail -n {count} {name}"
    else:
        guard = f"command -v journalctl >/dev/null 2>&1 || exit {_ABSENT}"
        journal = "journalctl --no-pager -o short-iso " + " ".join(
            f"-u {shlex.quote(unit)}" for unit in (source.name, *source.legacy_names)
        )
        read = f"{journal} | {grep} | tail -n {count}" if pattern else f"{journal} -n {count}"
    return f"{guard}; {read}"


def collect_log(
    conn: ServerConnection,
    source: LogSource,
    *,
    lines: int,
    pattern: str = "",
    known_secrets: Iterable[str] = (),
) -> LogTail:
    """Read one source's tail and redact it; an unreachable server or missing source is an error, not empty."""
    try:
        result = conn.run(tail_command(source, lines=lines, pattern=pattern), timeout=60 if pattern else 30)
    except (OSError, MeridianError) as exc:
        return LogTail(source, error=f"could not run the command: {exc}")
    if result.returncode == _ABSENT:
        missing = {"container": "container not found", "file": "log file not found", "unit": "journalctl not found"}
        return LogTail(source, error=missing[source.kind])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        reason = detail[-1] if detail else f"exit {result.returncode}"
        return LogTail(source, error=redact_diagnostic_text(_CONTROL_RE.sub("", reason), known_secrets))
    text = redact_diagnostic_text(result.stdout, known_secrets)
    kept = (_CONTROL_RE.sub("", line) for line in text.splitlines())
    return LogTail(source, lines=tuple(line for line in kept if line.strip() and line.strip() != _JOURNAL_EMPTY))
