"""`meridian fleet history` (hidden while the control service is): the service's recorded revisions."""

from __future__ import annotations

from rich import box
from rich.markup import escape
from rich.table import Table

from meridian.commands._helpers import load_topology_cluster
from meridian.console import err_console, error_context, fail, info, is_json_mode
from meridian.control.client import ControlClient, ControlServiceError
from meridian.control.models import RevisionSummary
from meridian.core.errors import LocalStateError
from meridian.core.output import OperationContext, command_envelope
from meridian.renderers import emit_json

COMMAND = "fleet.history"
NOT_RECORDED = "Revisions are recorded only on control-service fleets."
PLAN_PREFIX = 12
_STATUS_STYLE = {"applied": "green", "failed": "red", "approved": "yellow", "proposed": "dim"}


def run_history() -> None:
    operation = OperationContext()
    with error_context(COMMAND, timer=operation.timer):
        _run_history(operation)


def _run_history(operation: OperationContext) -> None:
    cluster = load_topology_cluster()
    if not cluster.runs_control_service:
        if is_json_mode():
            emit_json(
                command_envelope(
                    command=COMMAND,
                    data={"recorded": False, "revisions": []},
                    summary=NOT_RECORDED,
                    timer=operation.timer,
                )
            )
        else:
            info(NOT_RECORDED)
        return
    if not cluster.panel.url or not cluster.panel.api_token:
        fail(
            LocalStateError(
                "The control service's saved credentials are missing.",
                hint="Finish the deploy first: revisions are recorded once the control service is up.",
            )
        )
    try:
        with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
            revisions = client.revisions()
    except ControlServiceError as exc:
        fail(exc)

    summary = f"{len(revisions)} revision{'s' if len(revisions) != 1 else ''}"
    if is_json_mode():
        emit_json(
            command_envelope(
                command=COMMAND,
                data={"recorded": True, "revisions": [item.to_data() for item in revisions]},
                summary=summary,
                timer=operation.timer,
            )
        )
        return
    if not revisions:
        info("No revisions recorded yet; the next apply records one.")
        return
    err_console.print()
    err_console.print(_table(revisions))
    err_console.print()


def _table(revisions: list[RevisionSummary]) -> Table:
    table = Table(title="Revisions", show_lines=False, pad_edge=False, box=box.ROUNDED, padding=(0, 2))
    table.add_column("#", justify="right", style="bold")
    table.add_column("Time", style="dim")
    table.add_column("Author")
    table.add_column("Status")
    table.add_column("Plan", style="dim")
    for item in revisions:
        approver = item.approved_by
        author = item.author.name
        if approver is not None and (approver.token_id, approver.name) != (item.author.token_id, item.author.name):
            author = f"{author}, approved by {approver.name}"
        style = _STATUS_STYLE.get(item.status, "")
        table.add_row(
            str(item.number),
            item.created_at.replace("T", " ").removesuffix("Z") + " UTC",
            escape(author),
            f"[{style}]{item.status}[/{style}]" if style else item.status,
            item.plan_hash[:PLAN_PREFIX],
        )
    return table
