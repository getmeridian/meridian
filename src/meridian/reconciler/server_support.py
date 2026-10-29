"""Helpers shared by the SSH-backed server resource drivers."""

from __future__ import annotations

import shlex

from meridian.compiler.models import (
    ResourcePlan,
)
from meridian.provision.ensure import ensure_file_content
from meridian.provision.steps import ProvisionContext, StepResult
from meridian.provision.warp import InstallWarp
from meridian.reconciler.resources import ResourceAction, ResourceReconcileError, UnknownResourceOutcome
from meridian.reconciler.server_evidence import managed_command_output
from meridian.ssh import ServerConnection


def runtime_pin(plan: ResourcePlan, name: str, fallback: str) -> str:
    return plan.deployment_contract.runtime_pins.get(name, fallback)


def runtime_pin_group(plan: ResourcePlan, prefix: str) -> dict[str, str]:
    """Runtime pins sharing *prefix*, keyed by the rest of their name (asset digests)."""
    return {
        name.removeprefix(prefix): value
        for name, value in plan.deployment_contract.runtime_pins.items()
        if name.startswith(prefix)
    }


def raise_if_timeout(action: ResourceAction, result: object | None) -> None:
    """A mutation that timed out or lost its SSH connection (exit 255) may have run: observe before retrying."""
    returncode = getattr(result, "returncode", None) if result is not None else None
    if returncode == 124:
        raise UnknownResourceOutcome(f"Timed out while mutating {action.resource.logical_id}; observation is required.")
    if returncode == 255:
        raise UnknownResourceOutcome(
            f"Lost the SSH connection while mutating {action.resource.logical_id}; observation is required."
        )


def raise_if_step_timeout(action: ResourceAction, result: StepResult) -> None:
    for command in result.commands:
        raise_if_timeout(action, command)
    if "timed out" in result.detail.casefold() or "timeout" in result.detail.casefold():
        raise UnknownResourceOutcome(f"Timed out while mutating {action.resource.logical_id}; observation is required.")


def restore_file(conn: ServerConnection, path: str, previous_content: str | None) -> None:
    """Put a file back as it was before a failed mutation (absent stays absent)."""
    if previous_content is None:
        conn.run(f"rm -f {shlex.quote(path)}", timeout=15)
        return
    ensure_file_content(conn, path, previous_content, mode="644", create_parent=True)


def warp_connected(logical_id: str, conn: ServerConnection) -> bool:
    """Whether WARP proxies egress; a missing `warp-cli` is absence, other failures unavailable evidence."""
    status = conn.run("warp-cli --accept-tos status 2>/dev/null", timeout=15)
    output = managed_command_output(logical_id, status, "WARP status")
    return output is not None and "Connected" in output


def ensure_warp(conn: ServerConnection, address: str) -> None:
    """Install and connect WARP in proxy mode (one path for panel nodes and node agents)."""
    result = InstallWarp().run(conn, ProvisionContext(ip=address))
    if result.status == "failed":
        raise ResourceReconcileError(f"Could not configure WARP: {result.detail}")
