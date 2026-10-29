"""Health of Meridian's own control service and its node agents (#95).

A control-service fleet runs systemd units instead of Remnawave containers, and
each node agent reports what it applied to the service. `meridian check` reads
both: the units over SSH, the agent's report over the admin API.
"""

from __future__ import annotations

import shlex
from datetime import datetime
from typing import TYPE_CHECKING

from meridian.control.models import NodeStatus
from meridian.core.verification import VerificationFinding
from meridian.core.version_skew import agent_version_differs, newer_service_hint, service_skew
from meridian.diagnostics.checks import CheckResult, command_evidence_unavailable
from meridian.diagnostics.evidence import finding

if TYPE_CHECKING:
    from meridian.core.execution import ServerConnection

# Agents also re-check what they applied every 5 minutes and repair drift, such as a stopped core.


def check_unit_active(conn: ServerConnection, unit: str, *, remediation: str) -> CheckResult:
    """Check that one systemd unit is active; unreadable state is skipped evidence."""
    result = conn.run(f"systemctl is-active {shlex.quote(unit)}", timeout=15)
    state = result.stdout.strip() or "unknown"
    if result.returncode == 0 and state == "active":
        return CheckResult(name=unit, status="passed", detail=f"{unit} is active")
    if command_evidence_unavailable(result.returncode):
        return CheckResult(name=unit, status="skipped", detail=f"Could not inspect {unit} over SSH")
    return CheckResult(
        name=unit,
        status="failed",
        detail=f"{unit} is {state}",
        remediation=remediation,
    )


def agent_finding(status: NodeStatus | None, *, logs_hint: str, now: datetime | None = None) -> VerificationFinding:
    """Judge one node agent from the service's record of it; *logs_hint* names the command reading its log."""
    if status is None:
        return finding("agent", "failed", "Node is not registered with the control service", "Re-run: meridian apply")
    if status.last_seen_at is None:
        return finding(
            "agent",
            "failed",
            "The node agent has never reached the control service",
            f"Check the agent: {logs_hint}",
        )
    if not status.is_live(now):
        return finding(
            "agent",
            "failed",
            f"The node agent last reached the control service {_ago(status.unseen_seconds(now) or 0)} ago",
            f"Check the agent: {logs_hint}",
        )
    if status.failed:
        return finding(
            "agent",
            "failed",
            "The node agent could not apply: " + "; ".join(status.errors or status.failed),
            f"Read the agent log ({logs_hint}), then re-run: meridian apply",
        )
    applied = status.applied.revision if status.applied is not None else None
    if not status.in_sync:
        return finding(
            "agent",
            "warning",
            f"The node agent has applied revision {applied or 'none'} of {status.revision}",
            f"Wait a minute and re-run the check; if it stays behind, read: {logs_hint}",
        )
    return finding("agent", "passed", f"The node agent applied revision {applied}")


def service_version_finding(cli_version: str, service_version: str) -> VerificationFinding:
    """Judge the CLI/service pair (`core/version_skew.py`): newer refuses applies, older is upgraded by one."""
    skew = service_skew(cli_version, service_version)
    if skew == "service_newer":
        return finding(
            "service_version",
            "failed",
            f"The control service runs Meridian {service_version}, newer than this Meridian ({cli_version}); "
            "this copy cannot apply",
            newer_service_hint(service_version),
        )
    if skew == "service_older":
        return finding(
            "service_version",
            "warning",
            f"The control service runs Meridian {service_version}; this Meridian ({cli_version}) upgrades it",
            "Run: meridian apply",
        )
    if skew == "unknown":
        return finding("service_version", "skipped", f"Cannot compare service {service_version} with {cli_version}")
    return finding("service_version", "passed", f"The control service runs this Meridian ({cli_version})")


def agent_version_finding(expected: str, status: NodeStatus | None) -> VerificationFinding | None:
    """A warning when a node reports another agent than this Meridian installs; None when nothing to say."""
    reported = status.agent_version if status is not None else None
    if not agent_version_differs(expected, reported):
        return None
    return finding(
        "agent_version",
        "warning",
        f"The node agent runs {reported}; this Meridian installs {expected}",
        "Run: meridian apply (the agent upgrades in place and keeps its identity)",
    )


def _ago(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 2:
        return f"{int(seconds)} s"
    if minutes < 120:
        return f"{minutes} min"
    return f"{minutes // 60} h"
