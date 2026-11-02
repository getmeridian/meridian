"""Single-person membership edits use the same frozen preview and atomic receipt as bulk edits."""

from __future__ import annotations

import typer
from pydantic import ValidationError

from meridian.commands._helpers import load_topology_cluster, require_intent
from meridian.commands.client_bulk import preview_table, reconcile_receipt, result_paths
from meridian.commands.client_handoff import resolve_client_mutation_name, resolve_client_name
from meridian.commands.client_limits import print_client_usage
from meridian.commands.profile_review import require_profile_backend
from meridian.console import confirm, fail, is_json_mode, ok, warn
from meridian.control.client import ControlClient, ControlServicePolicyError, ControlServiceUncertain
from meridian.control_access import access_panel
from meridian.core.clients import ClientLimitsResult, build_client_record, client_metadata_paths
from meridian.core.errors import MeridianError
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.people_bulk import BulkAction, BulkCommitRequest, BulkPreviewRequest, ClientBulkResult
from meridian.i18n import t
from meridian.node_commands import wait_for_flow_blocks
from meridian.renderers import emit_json


def run_set_profiles(name: str, profiles: list[str], *, dry_run: bool, yes: bool, operation: OperationContext) -> None:
    try:
        action = BulkAction(action="set-profiles", profiles=profiles)
    except ValidationError:
        fail(t("profiles.membership_invalid"), hint_type="user")
    if is_json_mode() and not yes and not dry_run:
        fail(t("profiles.requires_yes"), hint_type="user")
    cluster = load_topology_cluster()
    require_profile_backend(require_intent(cluster), requested=True)
    try:
        resolve_client_name(cluster, name, console_people=True)
        with access_panel(cluster) as panel:
            name = resolve_client_mutation_name(cluster, panel, name)
            user = panel.get_user(name)
            if user is None:
                fail(t("profiles.person_unknown", name=name), hint_type="user")
            declared = cluster.topology_intent is not None and name in cluster.topology_intent.access.users
            with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
                preview = client.preview_bulk(BulkPreviewRequest(action=action, usernames=[name]))
                if not is_json_mode():
                    preview_table(preview.people)
                if dry_run:
                    result = ClientLimitsResult(
                        client=build_client_record(user, declared=declared), profile_preview=preview, applied=False
                    )
                else:
                    if not yes and not confirm(t("profiles.membership_confirm", name=name), default=False):
                        raise typer.Exit(1)
                    request = BulkCommitRequest(preview_id=preview.id, preview_hash=preview.preview_hash)
                    try:
                        receipt = client.commit_bulk(request)
                    except ControlServiceUncertain:
                        receipt = reconcile_receipt(client, preview.id)
                    updated = panel.get_user(name)
                    assert updated is not None
                    blocks = client.user_flow_blocks(name)
                    blocks = wait_for_flow_blocks(lambda: client.user_flow_blocks(name), blocks)
                    result = ClientLimitsResult(
                        client=build_client_record(updated, declared=declared),
                        profile_receipt=receipt,
                        access=client.user_access(name),
                        flow_blocks=blocks,
                    )
    except ControlServicePolicyError as exc:
        fail(t(f"delivery.error_{exc.reason}"), hint_type="user")
    except MeridianError as exc:
        fail(exc)
    if is_json_mode():
        metadata = result_paths(
            ClientBulkResult(preview=result.profile_preview, receipt=result.profile_receipt, applied=not dry_run)
        )
        reveal = client_metadata_paths() | {
            path.replace("data.preview.", "data.profile_preview.").replace("data.receipt.", "data.profile_receipt.")
            for path in metadata
        }
        emit_json(
            command_envelope(
                command="client.set",
                data=result.to_data(),
                summary=Summary(
                    text="Reviewed profile membership",
                    changed=bool(result.profile_receipt and result.profile_receipt.counts.changed),
                ),
                timer=operation.timer,
            ),
            reveal=reveal,
        )
    elif dry_run:
        ok(t("bulk.preview_only"))
    else:
        print_client_usage(result.client)
        ok(t("profiles.membership_changed", name=name))
        labels = {
            "blocked": "node_commands.flow_blocked",
            "pending_node": "node_commands.flow_pending",
            "agent_too_old": "node_commands.flow_old_agent",
        }
        for node in result.flow_blocks.nodes if result.flow_blocks is not None else []:
            (ok if node.status == "blocked" else warn)(node.node_id + ": " + t(labels[node.status]))
