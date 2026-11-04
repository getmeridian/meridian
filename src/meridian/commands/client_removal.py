"""Named client removals use the service's existing frozen bulk review and receipt."""

from __future__ import annotations

import typer

from meridian.cluster import ClusterConfig
from meridian.commands.client_bulk import preview_table, reconcile_receipt, result_paths
from meridian.console import confirm, is_json_mode, ok
from meridian.control.client import ControlClient, ControlServiceUncertain
from meridian.core.clients import ClientReference, ClientRemoveResult
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.people_bulk import BulkAction, BulkCommitRequest, BulkPreviewRequest, ClientBulkResult
from meridian.people_adoption import require_people_service
from meridian.renderers import emit_json


def run_reviewed_removal(
    names: list[str], cluster: ClusterConfig, operation: OperationContext, *, yes: bool, dry_run: bool
) -> None:
    with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
        require_people_service(client)
        preview = client.preview_bulk(BulkPreviewRequest(action=BulkAction(action="delete"), usernames=names))
        if not is_json_mode():
            preview_table(preview.people)
        receipt = None
        if not dry_run:
            shown = ", ".join(person.display_name for person in preview.people)
            if not yes and not confirm(f"Permanently remove {shown}?"):
                raise typer.Exit(1)
            try:
                receipt = client.commit_bulk(
                    BulkCommitRequest(preview_id=preview.id, preview_hash=preview.preview_hash)
                )
            except ControlServiceUncertain:
                receipt = reconcile_receipt(client, preview.id)
    result = ClientRemoveResult(
        clients=[
            ClientReference(username=person.username, display_name=person.display_name) for person in preview.people
        ],
        review_hash=preview.preview_hash,
        preview=preview,
        receipt=receipt,
        applied=not dry_run,
    )
    if is_json_mode():
        emit_json(
            command_envelope(
                command="client.remove",
                data=result.to_data(),
                summary=Summary(
                    text="Reviewed client removal",
                    changed=receipt is not None,
                    counts={"removed": receipt.counts.changed if receipt else 0},
                ),
                timer=operation.timer,
            ),
            reveal=result_paths(ClientBulkResult(preview=preview, receipt=receipt, applied=not dry_run)),
        )
    elif dry_run:
        ok("Review only; nobody was removed.")
    else:
        ok(f"Removed {len(names)} people. Their links no longer work.")
