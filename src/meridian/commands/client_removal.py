"""One local review and apply for several names or a committed-topology removal draft."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.markup import escape

from meridian import config
from meridian.cluster import ClusterConfig
from meridian.commands._helpers import apply_reviewed_intent, require_intent, reviewed_runtime
from meridian.console import confirm, err_console, fail, is_json_mode, ok
from meridian.control.client import ControlClient
from meridian.core.agent_types import canonical_sha256
from meridian.core.clients import ClientReference, ClientRemoveResult
from meridian.core.errors import MeridianError
from meridian.core.models import OutputEnvelope, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.people_bulk import BulkPreview, BulkReceipt, ClientBulkResult
from meridian.core.people_removal import PeopleRemovalProposal
from meridian.core.setup import SetupDraft
from meridian.i18n import isolate, t
from meridian.people_removal import require_removal_fence, require_removal_revision, require_removal_users
from meridian.renderers import emit_json
from meridian.servers import ServerRegistry
from meridian.setup.editor import remove_access_user_from_intent

MAX_PROPOSAL_FILE_BYTES = 64 * 1024 * 1024


def _reviewed_proposal(view: BulkPreview | BulkReceipt) -> PeopleRemovalProposal:
    proposal = view.proposal
    if proposal is None:
        raise ValueError
    usernames = sorted(person.username for person in view.people if person.outcome == "cli_required")
    if (
        view.action.action != "delete"
        or proposal.usernames != usernames
        or view.counts.cli_required != len(usernames)
        or (
            isinstance(view, BulkPreview)
            and view.preview_hash != canonical_sha256(view.model_dump(mode="json", exclude={"preview_hash"}))
        )
    ):
        raise MeridianError(t("bulk.proposal_review_invalid"), category="user")
    return proposal


def read_removal_proposal(path: str) -> PeopleRemovalProposal:
    """Accept the saved proposal, API preview/receipt, or CLI bulk output envelope."""
    try:
        with Path(path).open("rb") as source:
            content = source.read(MAX_PROPOSAL_FILE_BYTES + 1)
        if len(content) > MAX_PROPOSAL_FILE_BYTES:
            raise ValueError
        body = json.loads(content.decode("utf-8"))
        if not isinstance(body, dict):
            raise ValueError
        if "data" in body:
            envelope = OutputEnvelope.model_validate(body)
            result = ClientBulkResult.model_validate(envelope.data)
            preview = _reviewed_proposal(result.preview) if result.preview is not None else None
            receipt = _reviewed_proposal(result.receipt) if result.receipt is not None else None
            if (
                result.preview is not None
                and result.receipt is not None
                and (result.preview.id, result.preview.preview_hash, preview)
                != (result.receipt.id, result.receipt.preview_hash, receipt)
            ):
                raise MeridianError(t("bulk.proposal_review_invalid"), category="user")
            proposal = receipt or preview
        elif "committed_at" in body:
            proposal = _reviewed_proposal(BulkReceipt.model_validate(body))
        elif "created_at" in body:
            proposal = _reviewed_proposal(BulkPreview.model_validate(body))
        else:
            proposal = PeopleRemovalProposal.model_validate(body)
        if proposal is None:
            raise ValueError
        return proposal
    except (OSError, UnicodeError, ValueError, ValidationError) as exc:
        raise MeridianError(t("bulk.proposal_invalid"), category="user") from exc


def require_current_removal(cluster: ClusterConfig, proposal: PeopleRemovalProposal) -> None:
    require_removal_fence(
        proposal,
        fleet_id=cluster.fleet_id,
        generation=cluster.active_generation,
        plan_hash=cluster.active_plan_hash,
        revision=proposal.revision,
    )
    require_removal_users(proposal.usernames, require_intent(cluster).access.users)
    if canonical_sha256(require_intent(cluster).model_dump(mode="json")) != proposal.intent_sha256:
        fail(t("bulk.proposal_stale"), hint_type="user")
    with ControlClient(cluster.panel.url, cluster.panel.api_token, report_mutations=False) as client:
        state = client.fence()
        record = state.record
        require_removal_fence(
            proposal,
            fleet_id=state.fleet_id,
            generation=record.generation if record else None,
            plan_hash=record.plan_hash if record else None,
            revision=record.revision if record else None,
        )
        require_removal_revision(proposal, client.revision(proposal.revision))


def run_reviewed_removal(
    names: list[str],
    cluster: ClusterConfig,
    operation: OperationContext,
    *,
    yes: bool,
    dry_run: bool,
    proposal: PeopleRemovalProposal | None,
) -> None:
    if proposal is not None:
        require_current_removal(cluster, proposal)
    intent = require_intent(cluster)
    require_removal_users(names, intent.access.users)
    if not set(intent.access.users) - set(names):
        fail(t("bulk.remove_last"), hint_type="user")
    updated = intent
    for name in names:
        updated = remove_access_user_from_intent(updated, name)
    registry = ServerRegistry(config.SERVER_PROFILES_FILE)
    review = reviewed_runtime(registry, cluster, removal_proposal=proposal).review(
        SetupDraft.from_intent(updated),
        cluster=cluster,
    )
    shown = ", ".join(isolate(intent.access.display_name(name)) for name in names)
    if not is_json_mode():
        err_console.print()
        err_console.print(escape(t("bulk.remove_review", people=shown)))
        for binding in review.retiring:
            err_console.print("  " + escape(t("bulk.remove_resource", name=binding.logical_id)))
        err_console.print("  " + escape(t("bulk.remove_hash", hash=review.review_hash)))
        err_console.print()
    if not dry_run:
        if not yes and not confirm(t("bulk.remove_confirm", people=shown)):
            raise typer.Exit(1)
        apply_reviewed_intent(
            cluster,
            registry,
            updated,
            expected_plan_hash=review.review_hash,
            removal_proposal=proposal,
            failure=t("bulk.remove_failed"),
            hint=t("bulk.remove_retry"),
        )
    result = ClientRemoveResult(
        clients=[ClientReference(username=name) for name in names],
        review_hash=review.review_hash,
        applied=not dry_run,
    )
    if is_json_mode():
        emit_json(
            command_envelope(
                command="client.remove",
                data=result.to_data(),
                summary=Summary(
                    text="Reviewed client removal",
                    changed=not dry_run,
                    counts={"removed": 0 if dry_run else len(names)},
                ),
                timer=operation.timer,
            )
        )
    else:
        ok(t("bulk.preview_only") if dry_run else t("bulk.remove_done", count=len(names)))
