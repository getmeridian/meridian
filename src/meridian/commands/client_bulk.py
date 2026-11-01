"""Preview, confirm, and commit service-owned bulk changes through typed API calls."""

from __future__ import annotations

from typing import Any

import typer
from pydantic import ValidationError
from rich.markup import escape
from rich.table import Table

from meridian.cli_people import people_query
from meridian.commands._helpers import load_topology_cluster
from meridian.commands.client_limits import parse_limit_options
from meridian.console import confirm, err_console, error_context, fail, is_json_mode, ok, warn
from meridian.control.access_limits import limit_update
from meridian.control.client import ControlClient, ControlServiceError, ControlServiceUncertain
from meridian.control.http_errors import validation_message
from meridian.core.errors import MeridianError
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.people_bulk import (
    BulkAction,
    BulkActionName,
    BulkCommitRequest,
    BulkFields,
    BulkPersonChange,
    BulkPreviewRequest,
    ClientBulkResult,
)
from meridian.core.queries import bounded_integer
from meridian.i18n import t
from meridian.renderers import emit_json


def parse_bulk_action(action: BulkActionName, options: dict[str, Any]) -> BulkAction:
    if action == "extend":
        params = {
            name: bounded_integer(options[name], "--" + name, 1, 3650 if name == "days" else 120)
            for name in ("days", "months")
            if options.get(name) is not None
        }
    elif action in ("expiry", "quota", "devices", "tag", "renewal"):
        change = parse_limit_options(
            limit=options.get("limit"),
            reset=options.get("reset"),
            expires=options.get("expires"),
            devices=options.get("devices"),
            tag=options.get("set_tag"),
            reset_day=options.get("reset_day"),
            renew_days=options.get("renew_days"),
            renew_months=options.get("renew_months"),
            renewals=options.get("renewals"),
            no_renew=options.get("no_renew", False),
        )
        update = limit_update(change)
        params = update.model_dump(include=update.model_fields_set)
    else:
        params = {}
    return BulkAction.model_validate({"action": action, **params})


def field_value(fields: BulkFields | None, name: str) -> str:
    if fields is None:
        return t("bulk.deleted")
    value = getattr(fields, name, None)
    if name == "status":
        return t(f"bulk.status.{fields.status}")
    if name == "device_limit":
        return t("bulk.inherit") if value is None else t("bulk.unlimited") if value == 0 else str(value)
    if name == "traffic_limit_bytes":
        return t("bulk.unlimited") if not value else f"{value} B"
    if name == "period_traffic":
        return f"{fields.period_traffic.upload_bytes} B ↑ / {fields.period_traffic.download_bytes} B ↓"
    return t("bulk.none") if value is None or value == "" else str(value)


def preview_table(people: list[BulkPersonChange], *, undo: bool = False) -> None:
    table = Table(title=t("bulk.preview_title"), show_lines=True)
    for key in ("person", "field", "before", "after"):
        table.add_column(t("bulk." + key))
    for person in people:
        if person.cli_command:
            table.add_row(escape(person.display_name), t("bulk.cli_required"), "", escape(person.cli_command))
        elif not person.changes:
            table.add_row(escape(person.display_name), t("bulk.unchanged"), "", "")
        else:
            for index, field in enumerate(person.changes):
                left, right = (person.after, person.before) if undo else (person.before, person.after)
                table.add_row(
                    escape(person.display_name) if index == 0 else "",
                    t(f"bulk.field.{field}"),
                    escape(field_value(left, field)),
                    escape(field_value(right, field)),
                )
        for warning in person.warnings:
            warn(t(f"bulk.warning.{warning}"))
    err_console.print(table)


def result_paths(result: ClientBulkResult) -> frozenset[str]:
    paths = set()
    for key in ("preview", "receipt"):
        view = getattr(result, key)
        if view is None:
            continue
        paths.add(f"data.{key}.action.tag")
        if key == "receipt" and view.undo_people:
            paths.update(
                f"data.receipt.undo_people.{index}.{side}.tag"
                for index in range(len(view.undo_people))
                for side in ("before", "after")
            )
        for index in range(len(view.people)):
            for side in ("before", "after"):
                paths.add(f"data.{key}.people.{index}.{side}.tag")
    return frozenset(paths)


def emit_result(command: str, result: ClientBulkResult, operation: OperationContext) -> None:
    view = result.receipt or result.preview
    assert view is not None
    if is_json_mode():
        emit_json(
            command_envelope(
                command=command,
                data=result.to_data(),
                summary=Summary(
                    text="Bulk people operation",
                    changed=result.applied and view.counts.changed > 0,
                    counts=view.counts.model_dump(),
                ),
                timer=operation.timer,
            ),
            reveal=result_paths(result),
        )
        return
    if result.receipt:
        ok(t("bulk.receipt", id=result.receipt.id))
        if result.receipt.undo_until and not result.receipt.undone_at:
            err_console.print(t("bulk.undo_hint", id=result.receipt.id))
        if result.receipt.undone_at:
            ok(t("bulk.undone"))
    else:
        ok(t("bulk.preview_only"))


def run_bulk(action: BulkActionName, options: dict[str, Any]) -> None:
    operation = OperationContext()
    command = "client.bulk." + action
    with error_context(command, timer=operation.timer):
        try:
            query = people_query(options)
            parsed = parse_bulk_action(action, options)
            persons = options.get("persons")
            if persons and any(
                query.selection_data()[key]
                for key in ("q", "status", "tag", "expires", "quota", "never", "attention", "lifecycle")
            ):
                fail(t("bulk.selection_conflict"), hint_type="user")
            request = BulkPreviewRequest(action=parsed, usernames=persons, query=None if persons else query)
        except (ValidationError, ValueError, MeridianError) as exc:
            fail(validation_message(exc) if isinstance(exc, ValidationError) else str(exc), hint_type="user")
        dry_run, yes = options.get("dry_run", False), options.get("yes", False)
        if is_json_mode() and not yes and not dry_run:
            fail(t("bulk.json_confirm"), hint_type="user")
        cluster = load_topology_cluster()
        try:
            with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
                review = client.preview_bulk(request)
                if not is_json_mode():
                    preview_table(review.people)
                    err_console.print(t("bulk.summary", **review.counts.model_dump()))
                    err_console.print(t("bulk.nodes", count=review.changed_nodes))
                    for warning in review.warnings:
                        warn(t(f"bulk.warning.{warning}"))
                if dry_run:
                    emit_result(command, ClientBulkResult(preview=review, applied=False), operation)
                    return
                if not yes and not confirm(t("bulk.confirm", count=review.counts.targeted)):
                    raise typer.Exit(1)
                try:
                    receipt = client.commit_bulk(
                        BulkCommitRequest(preview_id=review.id, preview_hash=review.preview_hash)
                    )
                except ControlServiceUncertain:
                    # Never repeat an uncertain write; a receipt is safe to read.
                    try:
                        receipt = client.bulk_receipt(review.id)
                    except ControlServiceError:
                        fail(t("bulk.uncertain", id=review.id), hint_type="system")
                emit_result(command, ClientBulkResult(preview=review, receipt=receipt, applied=True), operation)
        except ControlServiceError as exc:
            fail(exc)


def run_receipt(receipt_id: str, *, undo: bool = False, yes: bool = False, dry_run: bool = False) -> None:
    operation = OperationContext()
    command = "client.bulk.undo" if undo else "client.bulk.receipt"
    with error_context(command, timer=operation.timer):
        if undo and is_json_mode() and not yes and not dry_run:
            fail(t("bulk.json_confirm"), hint_type="user")
        cluster = load_topology_cluster()
        try:
            with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
                receipt = client.bulk_receipt(receipt_id)
                if not is_json_mode():
                    preview_table(receipt.people, undo=undo)
                if undo and not dry_run:
                    if receipt.undo_until is None or receipt.undone_at:
                        fail(t("bulk.undo_unavailable"), hint_type="user")
                    if not yes and not confirm(t("bulk.undo_confirm", count=receipt.counts.changed)):
                        raise typer.Exit(1)
                    receipt = client.undo_bulk(receipt_id)
                emit_result(command, ClientBulkResult(receipt=receipt, applied=undo and not dry_run), operation)
        except ControlServiceError as exc:
            fail(exc)
