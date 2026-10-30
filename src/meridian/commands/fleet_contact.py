"""Read or apply a reviewed edit to the owner's public contact."""

from __future__ import annotations

import typer
from pydantic import ValidationError
from rich.markup import escape

from meridian import config, console
from meridian.commands._helpers import apply_reviewed_intent, load_topology_cluster, require_intent, reviewed_runtime
from meridian.core.apply import CompiledApplyPreview, build_compiled_apply_result
from meridian.core.errors import MeridianError
from meridian.core.fleet_contact import FleetContactRequest, FleetContactResult
from meridian.core.models import OutputStatus, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.owner_contact import OwnerContact
from meridian.core.setup import SetupDraft
from meridian.core.topology import SetupIntent
from meridian.i18n import t
from meridian.renderers import emit_json
from meridian.servers import ServerRegistry

COMMAND = "fleet.contact"


def run_contact(*, text: str | None = None, link: str | None = None, dry_run: bool = False, yes: bool = False) -> None:
    operation = OperationContext()
    with console.error_context(COMMAND, timer=operation.timer):
        try:
            request = FleetContactRequest(text=text, link=link, dry_run=dry_run, yes=yes)
        except ValidationError as exc:
            field = exc.errors()[0]["loc"][0]
            key = "fleet_contact.text_invalid" if field == "text" else "fleet_contact.link_invalid"
            console.fail(t(key), hint_type="user")
        _run(request, operation)


def _run(request: FleetContactRequest, operation: OperationContext) -> None:
    if not request.is_edit and request.dry_run:
        console.fail(t("fleet_contact.choose_for_review"), hint_type="user")
    cluster = load_topology_cluster()
    current = require_intent(cluster)
    contact = request.updated(current.delivery.owner_contact)
    result = FleetContactResult(owner_contact=contact, dry_run=request.dry_run)
    if not request.is_edit:
        _emit(result, operation, status="ok", text="Read owner contact")
        _print_contact(contact)
        return
    if contact == current.delivery.owner_contact:
        _emit(result, operation, status="no_changes", text="Owner contact is unchanged")
        if not console.is_json_mode():
            console.ok(t("fleet_contact.unchanged"))
        _print_contact(contact)
        return
    if not request.dry_run and not request.yes and (console.is_json_mode() or not console.has_tty()):
        console.fail(t("fleet_contact.needs_yes"), hint=t("fleet_contact.needs_yes_hint"), hint_type="user")
    data = current.model_dump(mode="json")
    data["delivery"]["owner_contact"] = contact.to_data()
    updated = SetupIntent.model_validate(data)
    registry = ServerRegistry(config.SERVER_PROFILES_FILE)
    try:
        review = reviewed_runtime(registry, cluster).review(SetupDraft.from_intent(updated), cluster=cluster)
    except MeridianError as exc:
        console.fail(exc)
    preview = CompiledApplyPreview(
        plan_hash=review.plan.plan_hash,
        resource_count=len(review.plan.resources),
        review_hash=review.review_hash,
        retiring_resources=[binding.logical_id for binding in review.retiring],
    )
    result = FleetContactResult(owner_contact=contact, dry_run=request.dry_run, review=preview)
    if not console.is_json_mode():
        console.info(t("fleet_contact.review_title"))
        console.info(t("fleet_contact.before"))
        _print_contact(current.delivery.owner_contact)
        console.info(t("fleet_contact.after"))
        _print_contact(contact)
        console.info(t("fleet_contact.public"))
        if preview.retiring_resources:
            console.info(t("fleet_contact.retiring", count=len(preview.retiring_resources)))
            for logical_id in preview.retiring_resources:
                console.err_console.print(f"    [red]remove[/red] {escape(logical_id)}")
        console.err_console.print(f"  [dim]Review {preview.review_hash}[/dim]")
    if request.dry_run:
        _emit(result, operation, status="ok", text="Reviewed owner contact")
        if not console.is_json_mode():
            console.info(t("fleet_contact.dry_run"))
        return
    if not request.yes and not console.confirm(t("fleet_contact.confirm"), default=False):
        raise typer.Exit(1)
    execution = apply_reviewed_intent(
        cluster,
        registry,
        updated,
        failure=t("fleet_contact.failed"),
        hint=t("fleet_contact.failed_hint"),
        expected_plan_hash=review.review_hash,
    )
    applied = build_compiled_apply_result(execution, exit_code=0, summary="Topology converged.")
    _emit(
        FleetContactResult(owner_contact=contact, review=preview, applied=applied),
        operation,
        status="changed",
        text="Applied owner contact",
    )
    if not console.is_json_mode():
        console.ok(t("fleet_contact.changed"))
        console.info(t("fleet_contact.refresh", hours=updated.delivery.update_interval_hours))


def _print_contact(contact: OwnerContact) -> None:
    if console.is_json_mode():
        return
    for label, value in ((t("fleet_contact.text"), contact.text), (t("fleet_contact.link"), contact.link)):
        console.err_console.print(f"  [bold]{escape(label)}[/bold]  {escape(value or t('fleet_contact.none'))}")


def _emit(result: FleetContactResult, operation: OperationContext, *, status: OutputStatus, text: str) -> None:
    if console.is_json_mode():
        emit_json(
            command_envelope(
                command=COMMAND,
                data=result.to_data(),
                summary=Summary(text=text, changed=status == "changed"),
                status=status,
                timer=operation.timer,
            ),
            reveal=frozenset({"data.owner_contact.text", "data.owner_contact.link"}),
        )
