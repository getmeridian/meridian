"""Helpers shared by the SSH-backed server resource drivers."""

from __future__ import annotations

import shlex
from typing import TypeVar

from meridian.compiler.models import (
    CertificatePayload,
    FirewallRulePayload,
    NginxArtifactPayload,
    NodeRuntimePayload,
    RealmHopPayload,
    ResourcePlan,
    ServerBaselinePayload,
)
from meridian.provision.ensure import ensure_file_content
from meridian.provision.steps import StepResult
from meridian.reconciler.resources import ResourceAction, ResourceReconcileError, UnknownResourceOutcome
from meridian.ssh import ServerConnection

PayloadT = TypeVar(
    "PayloadT",
    FirewallRulePayload,
    CertificatePayload,
    NginxArtifactPayload,
    RealmHopPayload,
    NodeRuntimePayload,
    ServerBaselinePayload,
)


def payload_of(action: ResourceAction, expected_type: type[PayloadT]) -> PayloadT:
    payload = action.resource.payload
    if not isinstance(payload, expected_type):
        raise ResourceReconcileError(f"Server driver received {payload.kind}, expected {expected_type.__name__}.")
    return payload


def runtime_pin(plan: ResourcePlan, name: str, fallback: str) -> str:
    return plan.deployment_contract.runtime_pins.get(name, fallback)


def raise_if_timeout(action: ResourceAction, result: object | None) -> None:
    if result is not None and getattr(result, "returncode", None) == 124:
        raise UnknownResourceOutcome(f"Timed out while mutating {action.resource.logical_id}; observation is required.")


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
