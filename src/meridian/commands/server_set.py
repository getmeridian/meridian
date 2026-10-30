"""Edit server metadata only through an exact reviewed topology apply."""

from __future__ import annotations

import typer
from pydantic import ValidationError
from rich.markup import escape

from meridian import config, console
from meridian.commands._helpers import apply_reviewed_intent, load_topology_cluster, require_intent, reviewed_runtime
from meridian.commands._validation import validate_command_input
from meridian.commands.server_display import print_metadata
from meridian.core.apply import CompiledApplyPreview, build_compiled_apply_result
from meridian.core.errors import MeridianError
from meridian.core.fleet_document import change_line, intent_changes
from meridian.core.models import OutputStatus, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.server_allowances import (
    ServerMetadata,
    ServerMetadataError,
    ServerSetRequest,
    ServerSetResult,
    build_server_metadata_change,
)
from meridian.core.setup import SetupDraft
from meridian.core.topology import SetupIntent
from meridian.i18n import t
from meridian.renderers import emit_json
from meridian.servers import ServerRegistry

COMMAND = "server.set"


def run_set(
    name: str,
    *,
    allowance: str | None = None,
    reset_day: str | None = None,
    provider: str | None = None,
    pay_on: str | None = None,
    pay_every: str | None = None,
    dry_run: bool = False,
    yes: bool = False,
) -> None:
    operation = OperationContext()
    with console.error_context(COMMAND, timer=operation.timer):
        try:
            change = build_server_metadata_change(
                allowance=allowance, reset_day=reset_day, provider=provider, pay_on=pay_on, pay_every=pay_every
            )
            request = validate_command_input(
                ServerSetRequest, t("server_set.invalid"), query=name, change=change, dry_run=dry_run, yes=yes
            )
            _run(request, operation)
        except ServerMetadataError as exc:
            if console.is_json_mode():
                console.fail(exc)
            console.fail(t(f"server_set.invalid_{exc.field}"), hint_type="user")
        except MeridianError as exc:
            console.fail(exc)
        except ValidationError as exc:
            console.fail(
                t("server_set.invalid") + ": " + str(exc.errors()[0]["msg"]).removeprefix("Value error, "),
                hint_type="user",
            )


def _server_ref(intent: SetupIntent, registry: ServerRegistry, query: str) -> str:
    refs = intent.declared_server_refs()
    if query in refs:
        return query
    entry = registry.find(query)
    if entry is not None:
        for ref in refs:
            if ref in {entry.id, entry.host, entry.name}:
                return ref
    console.fail(t("server_set.unknown", name=query), hint=t("server_set.unknown_hint"), hint_type="user")


def _run(request: ServerSetRequest, operation: OperationContext) -> None:
    cluster = load_topology_cluster()
    current = require_intent(cluster)
    if current.control.backend != "meridian":
        console.fail(t("server_set.needs_service"), hint_type="user")
    registry = ServerRegistry(config.SERVER_PROFILES_FILE)
    ref = _server_ref(current, registry, request.query)
    previous = current.server_metadata.get(ref, ServerMetadata())
    metadata = request.change.apply_to(previous)
    result = ServerSetResult(server_ref=ref, metadata=metadata, dry_run=request.dry_run)
    if not request.change.model_fields_set:
        if request.dry_run:
            console.fail(t("server_set.choose_for_review"), hint_type="user")
        _emit(result, operation, "ok", "Read reviewed server metadata")
        print_metadata(metadata)
        return
    if metadata == previous:
        _emit(result, operation, "no_changes", "Server metadata is unchanged")
        if not console.is_json_mode():
            console.ok(t("server_set.unchanged", name=request.query))
            print_metadata(metadata)
        return
    if not request.dry_run and not request.yes and (console.is_json_mode() or not console.has_tty()):
        console.fail(
            t("server_set.needs_yes"), hint=t("server_set.needs_yes_hint", name=request.query), hint_type="user"
        )
    data = current.model_dump(mode="json")
    data.setdefault("server_metadata", {})[ref] = metadata.model_dump(mode="json")
    if metadata == ServerMetadata():
        data["server_metadata"].pop(ref)
    updated = SetupIntent.model_validate(data)
    review = reviewed_runtime(registry, cluster).review(SetupDraft.from_intent(updated), cluster=cluster)
    preview = CompiledApplyPreview(
        plan_hash=review.plan.plan_hash,
        resource_count=len(review.plan.resources),
        review_hash=review.review_hash,
        retiring_resources=[binding.logical_id for binding in review.retiring],
    )
    result = ServerSetResult(server_ref=ref, metadata=metadata, dry_run=request.dry_run, review=preview)
    if not console.is_json_mode():
        console.info(t("server_set.review", name=request.query))
        for change in intent_changes(current, updated):
            console.err_console.print("    " + escape(change_line(change)))
        print_metadata(metadata)
        if metadata.allowance_bytes is not None and (
            previous.allowance_bytes is None or previous.reset_day != metadata.reset_day
        ):
            console.info(t("server_set.tracking_starts"))
        for logical_id in preview.retiring_resources:
            console.err_console.print(f"    [red]remove[/red] {escape(logical_id)}")
        console.err_console.print(f"  [dim]Review {preview.review_hash}[/dim]")
    if request.dry_run:
        _emit(result, operation, "ok", "Reviewed server metadata")
        return
    if not request.yes and not console.confirm(t("server_set.confirm", name=request.query), default=False):
        raise typer.Exit(1)
    execution = apply_reviewed_intent(
        cluster,
        registry,
        updated,
        failure=t("server_set.failed"),
        hint=t("server_set.failed_hint"),
        expected_plan_hash=review.review_hash,
    )
    _emit(
        ServerSetResult(
            server_ref=ref,
            metadata=metadata,
            review=preview,
            applied=build_compiled_apply_result(execution, exit_code=0, summary="Topology converged."),
        ),
        operation,
        "changed",
        "Applied reviewed server metadata",
    )
    if not console.is_json_mode():
        console.ok(t("server_set.changed", name=request.query))


def _emit(result: ServerSetResult, operation: OperationContext, status: OutputStatus, text: str) -> None:
    if console.is_json_mode():
        emit_json(
            command_envelope(
                command=COMMAND,
                data=result.to_data(),
                summary=Summary(text=text, changed=status == "changed"),
                status=status,
                timer=operation.timer,
            )
        )
