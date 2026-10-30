"""`meridian fleet export` and `meridian fleet import` -- the fleet's topology as an editable file.

Export prints the reviewed topology intent as YAML with saved server names; import
shows what an edited copy changes, then applies it through the same review, hash,
and retirement as `apply`. Clients removed from the file lose their connection
page, as with `client remove`; clients added to it get one, as with `client add`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.commands._helpers import (
    apply_reviewed_intent,
    forget_unreachable_servers,
    load_topology_cluster,
    require_intent,
    reviewed_runtime,
)
from meridian.console import confirm, err_console, error_context, fail, has_tty, info, is_json_mode, ok, warn
from meridian.core.apply import CompiledApplyPreview, build_compiled_apply_result
from meridian.core.errors import MeridianError
from meridian.core.fleet_document import (
    FleetExportResult,
    FleetImportResult,
    IntentChange,
    change_line,
    intent_changes,
    intent_server_refs,
    relabel_servers,
)
from meridian.core.models import ErrorModel, OutputStatus, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.topology import SetupIntent
from meridian.core.validation import CoreInputError
from meridian.i18n import t
from meridian.renderers import emit_json
from meridian.servers import ServerRegistry

EXPORT_COMMAND = "fleet.export"
IMPORT_COMMAND = "fleet.import"
# Public configuration fields remain complete in the final export envelope.
_REVEALED_KEYS = frozenset(
    {"probe_url", "data.document.delivery.owner_contact.text", "data.document.delivery.owner_contact.link"}
)
_STYLES = {"add": "green", "remove": "red", "change": "yellow"}


def run_export() -> None:
    """Print the fleet document: YAML on stdout, or the JSON envelope."""
    from meridian import config
    from meridian.setup.fleet_document import dump_fleet_document, fleet_document

    operation = OperationContext()
    with error_context(EXPORT_COMMAND, timer=operation.timer):
        intent = require_intent(load_topology_cluster())
        registry = ServerRegistry(config.SERVER_PROFILES_FILE)
        try:
            document = fleet_document(intent, registry)
            unsaved = [ref for ref in intent_server_refs(intent) if registry.find(ref) is None]
        except MeridianError as exc:
            fail(exc)
        for ref in unsaved:
            warn(t("fleet_export.unsaved_server", server=ref))
        if is_json_mode():
            emit_json(
                command_envelope(
                    command=EXPORT_COMMAND,
                    data=FleetExportResult(document=document).to_data(),
                    summary=Summary(text="Exported the fleet document", changed=False),
                    timer=operation.timer,
                ),
                reveal=_REVEALED_KEYS,
            )
            return
        sys.stdout.write(dump_fleet_document(document))
        sys.stdout.flush()


def run_import(source: str, *, dry_run: bool = False, yes: bool = False, forget_unreachable: bool = False) -> None:
    """Show what a fleet file changes, confirm, then apply exactly that review."""
    operation = OperationContext()
    with error_context(IMPORT_COMMAND, timer=operation.timer):
        _import(source, dry_run=dry_run, yes=yes, forget_unreachable=forget_unreachable, operation=operation)


def _import(source: str, *, dry_run: bool, yes: bool, forget_unreachable: bool, operation: OperationContext) -> None:
    from meridian import config
    from meridian.core.setup import SetupDraft
    from meridian.setup.fleet_document import intent_from_document, parse_fleet_document, server_labels

    text = _read(source)
    cluster = load_topology_cluster()
    current = require_intent(cluster)
    registry = ServerRegistry(config.SERVER_PROFILES_FILE)
    try:
        updated = intent_from_document(parse_fleet_document(text), current, registry)
        labels = server_labels(registry, [*intent_server_refs(current), *intent_server_refs(updated)])
    except CoreInputError as exc:
        fail(str(exc), hint=exc.hint, hint_type="user")
    except MeridianError as exc:
        fail(exc)
    changes = intent_changes(relabel_servers(current, labels), relabel_servers(updated, labels))
    if not changes:
        _emit_result(FleetImportResult(dry_run=dry_run), operation, text="No changes")
        if not is_json_mode():
            ok(t("fleet_import.no_changes"))
        return
    _refuse_unsupported(current, updated, labels)
    added = [change.target for change in changes if (change.subject, change.action) == ("client", "add")]

    if not dry_run:
        # Checked once there is something to apply: an unedited file says "No changes" anywhere.
        if not yes and (is_json_mode() or not has_tty()):
            fail(t("fleet_import.needs_yes"), hint=t("fleet_import.needs_yes_hint"), hint_type="user")
        _check_camouflage(registry, current, updated)
        if added:
            _refuse_unmanaged_clients(cluster, added)
        forget_unreachable_servers(cluster, registry, updated, forget=forget_unreachable, yes=yes)
    try:
        review = reviewed_runtime(registry, cluster).review(SetupDraft.from_intent(updated), cluster=cluster)
    except MeridianError as exc:
        fail(exc)
    preview = CompiledApplyPreview(
        plan_hash=review.plan.plan_hash,
        resource_count=len(review.plan.resources),
        review_hash=review.review_hash,
        retiring_resources=[binding.logical_id for binding in review.retiring],
    )
    if not is_json_mode():
        _print_review(source, changes, preview)
    if dry_run:
        _emit_result(FleetImportResult(dry_run=True, changes=changes, review=preview), operation, text="Dry run")
        if not is_json_mode():
            info(t("fleet_import.dry_run", file=source))
        return
    if not yes and not confirm(t("fleet_import.confirm"), default=False):
        raise typer.Exit(1)

    result = apply_reviewed_intent(
        cluster,
        registry,
        updated,
        failure=t("fleet_import.failed"),
        hint=t("fleet_import.failed_hint"),
        expected_plan_hash=review.review_hash,
    )
    summary = t("fleet_import.applied", file=source, count=len(changes))
    applied = build_compiled_apply_result(result, exit_code=0, summary="Topology converged.")
    _emit_result(
        FleetImportResult(changes=changes, review=preview, applied=applied),
        operation,
        text=f"Applied {len(changes)} changes",  # JSON stays English; the human line is translated
        changed=True,
    )
    if not is_json_mode():
        ok(summary)
        if added:
            info(t("fleet_import.new_clients"))


def _print_review(source: str, changes: list[IntentChange], preview: CompiledApplyPreview) -> None:
    err_console.print()
    err_console.print(f"  [bold]{escape(t('fleet_import.title', file=source))}[/bold]")
    for change in changes:
        style = _STYLES[change.action]
        err_console.print(f"  [{style}]{escape(change_line(change))}[/{style}]")
    if preview.retiring_resources:
        err_console.print(f"  {escape(t('fleet_import.retiring', count=len(preview.retiring_resources)))}")
        for logical_id in preview.retiring_resources:
            err_console.print(f"    [red]remove[/red] {escape(logical_id)}")
    err_console.print(f"  [dim]Review {preview.review_hash}[/dim]")


def _refuse_unsupported(current: SetupIntent, updated: SetupIntent, labels: dict[str, str]) -> None:
    """Refuse edits that need their own command, before anything connects."""
    from meridian.commands.sni import refuse_unusable_camouflage

    old_control, new_control = current.control, updated.control
    if (old_control.server_ref, old_control.public_hostname, old_control.backend) != (
        new_control.server_ref,
        new_control.public_hostname,
        new_control.backend,
    ):
        fail(t("fleet_import.control_changed"), hint=t("fleet_import.control_changed_hint"), hint_type="user")
    kept = {exit_.id: exit_ for exit_ in updated.exits}
    for exit_ in current.exits:
        new = kept.get(exit_.id)
        if new is None and exit_.server_ref == current.control.server_ref:
            fail(
                t("fleet_import.control_exit", exit=exit_.id),
                hint=t("fleet_import.control_exit_hint"),
                hint_type="user",
            )
        if new is not None and new.server_ref != exit_.server_ref:
            old_label, new_label = labels.get(exit_.server_ref, exit_.server_ref), labels.get(new.server_ref, "")
            fail(
                t("fleet_import.exit_moved", exit=exit_.id, old=old_label, new=new_label),
                hint=t("fleet_import.exit_moved_hint", exit=exit_.id, new=new_label),
                hint_type="user",
            )
    for sni in sorted(_new_borrowed_snis(current, updated)):
        refuse_unusable_camouflage(sni)


def _new_borrowed_snis(current: SetupIntent, updated: SetupIntent) -> set[str]:
    """Camouflage names the file introduces that belong to someone else's site."""
    before = {path.reality_sni for exit_ in current.exits for path in exit_.paths}
    before |= {relay.reality_sni for relay in current.transparent_relays}
    names = {path.reality_sni for exit_ in updated.exits for path in exit_.paths if not path.self_steal}
    names |= {relay.reality_sni for relay in updated.transparent_relays}
    return {name for name in names if name and name not in before}


def _check_camouflage(registry: ServerRegistry, current: SetupIntent, updated: SetupIntent) -> None:
    """Check new camouflage names from the servers that will dial them, as `node sni` and `relay sni` do."""
    from meridian.commands.sni import require_own_hostname, require_reachable_camouflage

    saved = {
        (exit_.id, path.protocol): (path.reality_sni, path.self_steal)
        for exit_ in current.exits
        for path in exit_.paths
    }
    exits = {exit_.id: exit_ for exit_ in updated.exits}
    for exit_ in updated.exits:
        for path in exit_.paths:
            if path.protocol != "reality" or saved.get((exit_.id, "reality")) == (path.reality_sni, path.self_steal):
                continue
            if path.self_steal:
                require_own_hostname(registry, exit_.server_ref, path.reality_sni)
                continue
            gateways = [
                gateway.server_ref for gateway in updated.routing_gateways if gateway.bridge_path_ref == path.id
            ]
            for server_ref in dict.fromkeys([exit_.server_ref, *gateways]):
                require_reachable_camouflage(registry, server_ref, path.reality_sni)
    relay_snis = {relay.id: relay.reality_sni for relay in current.transparent_relays}
    for relay in updated.transparent_relays:
        if relay.reality_sni and relay_snis.get(relay.id) != relay.reality_sni and relay.exit_ref in exits:
            require_reachable_camouflage(registry, exits[relay.exit_ref].server_ref, relay.reality_sni)


def _refuse_unmanaged_clients(cluster: ClusterConfig, added: list[str]) -> None:
    """Refuse a new client whose name an unmanaged panel user already holds, as `client add` does."""
    from meridian.commands.client_handoff import get_user_or_fail
    from meridian.control_access import access_panel

    with access_panel(cluster) as panel:
        for name in added:
            if get_user_or_fail(panel, name, action="check") is not None:
                fail(
                    t("fleet_import.client_taken", name=name),
                    hint=t("fleet_import.client_taken_hint"),
                    hint_type="user",
                )


def _emit_result(
    result: FleetImportResult,
    operation: OperationContext,
    *,
    text: str,
    changed: bool = False,
    warnings: list[ErrorModel] | None = None,
) -> None:
    if not is_json_mode():
        return
    status: OutputStatus = "changed" if changed else "no_changes" if not result.changes else "ok"
    contact_paths = frozenset(
        f"data.changes.{index}.{field}"
        for index, change in enumerate(result.changes)
        if change.subject == "delivery" and change.field == "owner_contact"
        for field in ("before", "after")
    )
    emit_json(
        command_envelope(
            command=IMPORT_COMMAND,
            data=result.to_data(),
            summary=Summary(text=text, changed=changed, counts={"changes": len(result.changes)}),
            status=status,
            exit_code=3 if warnings else 0,
            warnings=warnings or None,
            timer=operation.timer,
        ),
        reveal=_REVEALED_KEYS | contact_paths,
    )


def _read(source: str) -> str:
    try:
        return sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        fail(
            t("fleet_import.cannot_read", file=source, reason=exc.strerror if isinstance(exc, OSError) else str(exc)),
            hint=t("fleet_import.cannot_read_hint"),
            hint_type="user",
        )
