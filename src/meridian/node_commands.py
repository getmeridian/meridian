"""Deployer-side command waiting and cut-off delivery; no CLI or SSH dependency."""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable, Sequence

from meridian.cluster import ClusterConfig
from meridian.control.client import ControlClient, ControlServiceError
from meridian.control.command_models import (
    CutOffPlan,
    NodeCommandOutcome,
    NodeCommandOutcomeStatus,
    NodeCommandRecord,
    ResetInboundCreate,
)
from meridian.core.agent_types import NodeStateRef
from meridian.core.models import ErrorModel
from meridian.core.redaction import redact_diagnostic_text

COMMAND_WAIT_SECONDS = 300


def cut_off_scope(cluster: ClusterConfig, username: str) -> CutOffPlan:
    with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
        return client.cut_off_scope(username)


def wait_for_commands(
    client: ControlClient,
    commands: Sequence[NodeCommandRecord],
    *,
    timeout: float = COMMAND_WAIT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> list[NodeCommandOutcome]:
    """Wait for all nodes together; never multiply the timeout by the number of nodes."""
    latest = {record.command.command_id: record for record in commands}
    unreadable: dict[str, str] = {}
    abandoned: set[str] = set()
    deadline = clock() + max(0.0, timeout)
    while True:
        pending = [
            record for key, record in latest.items() if record.status in {"waiting", "pending"} and key not in abandoned
        ]
        if not pending:
            break
        for record in pending:
            if clock() >= deadline:
                break
            command_id = record.command.command_id
            try:
                latest[command_id] = client.command(record.node_id, command_id)
                unreadable.pop(command_id, None)
            except ControlServiceError as exc:
                unreadable[command_id] = redact_diagnostic_text(str(exc))[:300]
                if not exc.retryable:
                    abandoned.add(command_id)
        if clock() >= deadline:
            break
        if all(record.status not in {"waiting", "pending"} or key in abandoned for key, record in latest.items()):
            break
        sleep(min(1.0, max(0.0, deadline - clock())))
    grouped: dict[str, list[NodeCommandRecord]] = defaultdict(list)
    for record in latest.values():
        grouped[record.node_id].append(record)
    outcomes: list[NodeCommandOutcome] = []
    for node_id, records in sorted(grouped.items()):
        status: NodeCommandOutcomeStatus
        errors = [
            record.result.error.message
            for record in records
            if record.result is not None and record.result.error is not None
        ]
        errors.extend(
            unreadable[record.command.command_id] for record in records if record.command.command_id in unreadable
        )
        if any(record.status in {"failed", "rejected"} for record in records):
            status = "failed"
        elif any(record.status != "succeeded" for record in records):
            status = "timed_out"
        elif any(record.result is not None and record.result.effect == "core_restart" for record in records):
            status = "core_restart"
        else:
            status = "done"
        outcomes.append(NodeCommandOutcome(node_id=node_id, status=status, commands=records, errors=errors))
    return outcomes


def wait_commands(cluster: ClusterConfig, commands: Sequence[NodeCommandRecord]) -> list[NodeCommandOutcome]:
    with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
        return wait_for_commands(client, commands)


def cut_off_after_apply(cluster: ClusterConfig, plan: CutOffPlan) -> list[NodeCommandOutcome]:
    """The topology apply owns deletion; queue resets afterwards against each node's resulting revision."""
    commands: list[NodeCommandRecord] = []
    failed: dict[str, list[str]] = {}
    with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
        for node in plan.nodes:
            try:
                status = client.node(node.node_id)
                if status is None or status.revision is None or status.sha256 is None:
                    raise ControlServiceError("The affected node has no current document.", category="system")
                wanted = NodeStateRef(fleet_epoch=status.fleet_epoch, revision=status.revision, sha256=status.sha256)
                for tag in node.inbound_tags:
                    commands.append(
                        client.issue_command(node.node_id, ResetInboundCreate(inbound_tag=tag, wait_for=wanted))
                    )
            except ControlServiceError as exc:
                # A lost POST reply may already have queued a command; never blindly issue another ID.
                failed[node.node_id] = [redact_diagnostic_text(str(exc), cluster.secret_values())[:300]]
        outcomes = {outcome.node_id: outcome for outcome in wait_for_commands(client, commands)}
    for node_id, errors in failed.items():
        previous = outcomes.get(node_id)
        outcomes[node_id] = NodeCommandOutcome(
            node_id=node_id,
            status="failed",
            commands=previous.commands if previous else [],
            errors=[*(previous.errors if previous else []), *errors],
        )
    return [outcomes[node_id] for node_id in sorted(outcomes)]


def command_exit_code(outcomes: Sequence[NodeCommandOutcome]) -> int:
    if any(outcome.status == "failed" for outcome in outcomes):
        return 4
    return 3 if any(outcome.status == "timed_out" for outcome in outcomes) else 0


def command_warnings(outcomes: Sequence[NodeCommandOutcome]) -> list[ErrorModel]:
    return [
        ErrorModel(
            code="MERIDIAN_NODE_COMMAND_" + outcome.status.upper(),
            category="system",
            message=f"Node {outcome.node_id}: " + ("; ".join(outcome.errors) or outcome.status.replace("_", " ")),
            hint="Read the node's agent report and retry once the node is ready.",
            retryable=outcome.status == "timed_out",
            exit_code=3 if outcome.status == "timed_out" else 4,
            details={"node_id": outcome.node_id},
        )
        for outcome in outcomes
        if outcome.status in {"failed", "timed_out"}
    ]
