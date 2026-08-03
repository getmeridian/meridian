"""Fail-closed parsing for SSH-backed resource observations."""

from __future__ import annotations

from collections.abc import Collection
from typing import NoReturn, Protocol

from meridian.compiler.models import canonical_hash
from meridian.diagnostics import command_evidence_unavailable
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceObservation,
    ResourceReconcileError,
    postcondition_key,
)


class CommandEvidence(Protocol):
    """Command-result fields required by observation classifiers."""

    returncode: int
    stdout: str
    stderr: str


_PERMISSION_FAILURES = (
    "permission denied",
    "operation not permitted",
    "must be root",
    "need to be root",
    "requires root privileges",
    "a password is required",
    "authentication is required",
    "no tty present",
)
_DAEMON_FAILURES = (
    "cannot connect to the docker daemon",
    "cannot connect to daemon",
    "could not connect to daemon",
    "daemon is not running",
    "docker daemon socket",
    "error during connect",
    "failed to connect to bus",
    "is the docker daemon running",
    "system has not been booted with systemd",
)
_TOOL_FAILURES = (
    "command not found",
    "executable file not found",
    ": not found",
)
_MISSING_FILE = ("no such file or directory", "not a directory")
_MISSING_CONTAINER = ("no such object", "no such container")
_NEGATIVE_SERVICE_STATES = {
    "activating",
    "deactivating",
    "failed",
    "inactive",
    "maintenance",
    "reloading",
    "unknown",
}


def baseline_check_command(name: str, command: str) -> str:
    """Keep Docker daemon errors visible without changing the assertion."""
    if name == "docker":
        return command.replace(">/dev/null 2>&1", ">/dev/null")
    return command


def assertion_matches(
    logical_id: str,
    result: CommandEvidence,
    description: str,
    *,
    negative_returncodes: Collection[int] = (1,),
) -> bool:
    """Classify a shell assertion without treating execution failure as drift."""
    require_available_evidence(logical_id, result, description)
    if result.returncode == 0:
        return True
    if result.returncode in negative_returncodes:
        return False
    _raise_unavailable(logical_id, description, f"inspection returned exit code {result.returncode}")


def file_content_or_absent(
    logical_id: str,
    result: CommandEvidence,
    description: str,
) -> str | None:
    """Return readable file content, or None only for a proven missing path."""
    require_available_evidence(logical_id, result, description)
    if result.returncode == 0:
        return result.stdout
    if result.returncode == 1 and any(marker in _output(result) for marker in _MISSING_FILE):
        return None
    _raise_unavailable(logical_id, description, f"file read returned exit code {result.returncode}")


def negated_path_is_absent(
    logical_id: str,
    result: CommandEvidence,
    description: str,
) -> bool:
    """Parse ``test ! -e`` while rejecting transport and permission failures."""
    require_available_evidence(logical_id, result, description)
    if result.returncode in {0, 1}:
        return result.returncode == 0
    _raise_unavailable(logical_id, description, f"path inspection returned exit code {result.returncode}")


def require_successful_evidence(
    logical_id: str,
    result: CommandEvidence,
    description: str,
) -> None:
    """Require a command whose nonzero exits never prove negative state."""
    require_available_evidence(logical_id, result, description)
    if result.returncode != 0:
        _raise_unavailable(logical_id, description, f"inspection returned exit code {result.returncode}")


def service_is_active(
    logical_id: str,
    result: CommandEvidence,
    description: str,
) -> bool:
    """Distinguish systemd's negative states from a failed daemon query."""
    require_available_evidence(logical_id, result, description)
    state = result.stdout.strip().casefold()
    if result.returncode == 0 and state == "active":
        return True
    if state in _NEGATIVE_SERVICE_STATES:
        return False
    if result.returncode == 4 and "could not be found" in _output(result):
        return False
    _raise_unavailable(logical_id, description, f"unrecognized systemd result (exit {result.returncode})")


def managed_command_output(
    logical_id: str,
    result: CommandEvidence,
    description: str,
) -> str | None:
    """Return output, treating rc 127 as a missing managed binary."""
    require_available_evidence(
        logical_id,
        result,
        description,
        missing_managed_binary=True,
    )
    if result.returncode == 127:
        return None
    if result.returncode == 0:
        return result.stdout
    _raise_unavailable(logical_id, description, f"managed command returned exit code {result.returncode}")


def docker_container_running(
    logical_id: str,
    result: CommandEvidence,
    container_name: str,
) -> bool:
    """Return container state only when Docker produced authoritative evidence."""
    description = f"Docker container {container_name}"
    require_available_evidence(logical_id, result, description)
    output = _output(result)
    if result.returncode == 1 and any(marker in output for marker in _MISSING_CONTAINER):
        return False
    if result.returncode == 0 and result.stdout.strip().casefold() in {"true", "false"}:
        return result.stdout.strip().casefold() == "true"
    _raise_unavailable(logical_id, description, f"Docker inspection returned exit code {result.returncode}")


def require_available_evidence(
    logical_id: str,
    result: CommandEvidence,
    description: str,
    *,
    missing_managed_binary: bool = False,
) -> None:
    """Raise when a result says the requested observation did not complete."""
    reason = _unavailable_reason(result, missing_managed_binary=missing_managed_binary)
    if reason:
        _raise_unavailable(logical_id, description, reason)


def port_is_listening(output: str, port: int) -> bool:
    """Return whether an ``ss`` listing contains the exact port."""
    suffix = f":{port}"
    return any(field.endswith(suffix) for line in output.splitlines() for field in line.split())


def build_observation(
    action: ResourceAction,
    *,
    remote_id: str,
    exists: bool,
    matches: bool,
    projection: object,
    satisfied: set[str],
) -> ResourceObservation:
    """Build the canonical server-resource observation projection."""
    keys = sorted(
        postcondition_key(condition.kind, condition.target_ref, condition.detail)
        for condition in action.resource.postconditions
        if condition.kind in satisfied
    )
    return ResourceObservation(
        exists=exists,
        observed_hash=action.expected_hash if matches else canonical_hash(projection),
        remote_id=remote_id,
        satisfied_postconditions=keys,
    )


def _unavailable_reason(
    result: CommandEvidence,
    *,
    missing_managed_binary: bool,
) -> str:
    if getattr(result, "timed_out", False) is True or result.returncode == 124:
        return "inspection timed out"
    if result.returncode == 255:
        return "the SSH transport failed"
    if result.returncode == 127 and missing_managed_binary:
        return ""
    if command_evidence_unavailable(result.returncode):
        return "a required inspection tool is missing"
    if result.returncode == 126:
        return "the inspection command could not execute"
    output = _output(result)
    if any(marker in output for marker in _PERMISSION_FAILURES):
        return "the inspection was denied"
    if any(marker in output for marker in _DAEMON_FAILURES):
        return "a required daemon could not be queried"
    if any(marker in output for marker in _TOOL_FAILURES):
        return "a required inspection tool is missing"
    return ""


def _output(result: CommandEvidence) -> str:
    return f"{getattr(result, 'stdout', '')}\n{getattr(result, 'stderr', '')}".casefold()


def _raise_unavailable(logical_id: str, description: str, reason: str) -> NoReturn:
    raise ResourceReconcileError(
        f"Could not observe {logical_id}: {description} evidence is unavailable ({reason}).",
        hint="Restore SSH access and the required server inspection tools, then retry.",
        retryable=True,
    )
