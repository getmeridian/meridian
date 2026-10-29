"""Replace a running node agent's binary in place, keeping its identity, with rollback (#95).

A new agent build never needs a new enrollment: the node key, certificate, and
bootstrap stay exactly as they are. The upgrade stages the binary beside the
running one, has the staged build check that it can read the node's files
(`netagent check`: bootstrap, credentials, state, traffic store; it writes
nothing and dials nothing), keeps the running binary as the previous one, swaps
the staged one in with a rename, and restarts the unit. It succeeds only when
the service records a hello or report from the new build (its version names its
sources, see `agent/build.rs`) and the unit's process runs the new image;
otherwise the previous binary comes back and the action fails.
"""

from __future__ import annotations

import json
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from meridian.control.client import ControlClient
from meridian.control.models import NodeStatus
from meridian.control_artifacts import AgentBinary
from meridian.host_runtime import AGENT_BINARY, AGENT_BOOTSTRAP, AGENT_STATE_DIR, AGENT_UNIT
from meridian.reconciler.resources import ResourceReconcileError, UnknownResourceOutcome
from meridian.ssh import ServerConnection

STAGED_BINARY = f"{AGENT_BINARY}.new"
PREVIOUS_BINARY = f"{AGENT_BINARY}.prev"
# The new agent says hello as it starts and reports right after; twice its report interval is generous.
UPGRADE_REPORT_TIMEOUT = 120.0


@dataclass(frozen=True)
class StagedAgent:
    """A staged build that checked the node's files: the version its hellos will carry."""

    version: str


def stage_agent(conn: ServerConnection, binary: AgentBinary, node_id: str) -> StagedAgent:
    """Upload the new build beside the running one and let it check the node; nothing changes on failure."""
    _done(conn.put_bytes(STAGED_BINARY, binary.data, mode="755", timeout=180), "upload the new agent")
    staged = shlex.quote(STAGED_BINARY)
    digest = conn.run(f"sha256sum {staged}", timeout=30)
    if digest.returncode != 0 or digest.stdout.split()[:1] != [binary.sha256]:
        _discard(conn)
        raise ResourceReconcileError("The uploaded agent does not match its SHA-256.", retryable=True)
    checked = conn.run(f"{staged} check --bootstrap {AGENT_BOOTSTRAP} --state-dir {AGENT_STATE_DIR}", timeout=30)
    report = _check_report(checked.stdout) if checked.returncode == 0 else None
    if report is None or report.get("enrolled") is not True or report.get("node_id") != node_id:
        _discard(conn)
        detail = (checked.stderr or checked.stdout).strip()[-200:] or "no usable report"
        raise ResourceReconcileError(
            f"The new agent cannot take over this node's files: {detail}",
            hint="The running agent was left in place; rebuild the agent (make agent) and apply again.",
        )
    return StagedAgent(version=str(report.get("version", "")))


def swap_in(conn: ServerConnection) -> None:
    """Keep the running binary as the previous one, rename the staged one over it, and restart the agent."""
    result = conn.run(
        f"cp -p {AGENT_BINARY} {PREVIOUS_BINARY} && mv -f {STAGED_BINARY} {AGENT_BINARY} && "
        f"systemctl daemon-reload && systemctl restart {AGENT_UNIT}",
        timeout=60,
    )
    _done(result, "swap in the new agent")


def wait_for_upgrade(
    conn: ServerConnection,
    client: ControlClient,
    node_id: str,
    *,
    staged: StagedAgent,
    binary: AgentBinary,
    before: NodeStatus | None,
    sleep: Callable[[float], None],
    interval: float,
) -> bool:
    """Whether the new build reached the service and runs as the unit's process before the deadline."""
    deadline = time.monotonic() + UPGRADE_REPORT_TIMEOUT
    while True:
        status = client.node(node_id)
        if _new_build_reported(status, staged, before) and running_sha256(conn) == binary.sha256:
            return True
        if time.monotonic() >= deadline:
            return False
        sleep(interval)


def roll_back(conn: ServerConnection) -> bool:
    """Restore the previous binary and restart it; the identity was never touched."""
    result = conn.run(
        f"mv -f {PREVIOUS_BINARY} {AGENT_BINARY} && systemctl restart {AGENT_UNIT}",
        timeout=60,
    )
    return result.returncode == 0


def finish_upgrade(conn: ServerConnection) -> None:
    """Drop the previous binary once the new one has proved itself (best effort)."""
    conn.run(f"rm -f {PREVIOUS_BINARY}", timeout=15)


def running_sha256(conn: ServerConnection) -> str:
    """SHA-256 of the image the agent unit's main process runs (a replaced file reads as the old image)."""
    result = conn.run(
        f'pid=$(systemctl show -p MainPID --value {AGENT_UNIT}); [ "${{pid:-0}}" != 0 ] && sha256sum /proc/$pid/exe',
        timeout=15,
    )
    words = result.stdout.split()
    return words[0] if result.returncode == 0 and words else ""


def _new_build_reported(status: NodeStatus | None, staged: StagedAgent, before: NodeStatus | None) -> bool:
    if status is None or status.last_seen_at is None or status.agent_version != staged.version:
        return False
    if before is None or before.agent_version != staged.version:
        return True
    # The same version string (a build without a build id): only a later sighting proves the restart.
    return status.last_seen_at != before.last_seen_at


def _check_report(stdout: str) -> dict[str, Any] | None:
    lines = stdout.strip().splitlines()
    try:
        data = json.loads(lines[-1]) if lines else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _discard(conn: ServerConnection) -> None:
    conn.run(f"rm -f {STAGED_BINARY}", timeout=15)


def _done(result: Any, what: str) -> None:
    if result.returncode in (124, 255):
        raise UnknownResourceOutcome(f"Lost the server while trying to {what}; observation is required.")
    if result.returncode != 0:
        raise ResourceReconcileError(f"Could not {what}: {result.stderr.strip()[:200]}")
