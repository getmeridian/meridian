"""Read or apply a reviewed edit to the owner's public contact."""

from __future__ import annotations

from pydantic import ValidationError
from rich.markup import escape

from meridian import console
from meridian.commands._helpers import load_topology_cluster
from meridian.core.errors import MeridianError
from meridian.core.fleet_contact import FleetContactRequest, FleetContactResult
from meridian.core.models import OutputStatus, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.owner_contact import OwnerContact
from meridian.i18n import t
from meridian.renderers import emit_json

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
    from meridian.commands.fleet_presentation import live_presentation, presentation_client, review_presentation
    from meridian.core.presentation import PresentationPatch, PresentationState

    cluster = load_topology_cluster()
    try:
        with presentation_client(cluster) as client:
            current = live_presentation(client)
            contact = request.updated(current.owner_contact)
            state, changed = current, False
            if request.is_edit:

                def show(old: PresentationState, new: PresentationState) -> None:
                    console.info(t("fleet_contact.before"))
                    _print_contact(old.owner_contact)
                    console.info(t("fleet_contact.after"))
                    _print_contact(new.owner_contact)
                    console.info(t("fleet_contact.public"))

                state, changed = review_presentation(
                    client,
                    current,
                    PresentationPatch(expected_version=current.version, owner_contact=contact),
                    dry_run=request.dry_run,
                    yes=request.yes,
                    show=show,
                )
    except MeridianError as exc:
        console.fail(exc)
    status: OutputStatus = (
        "changed" if changed else "no_changes" if request.is_edit and contact == current.owner_contact else "ok"
    )
    _emit(
        FleetContactResult(owner_contact=state.owner_contact, version=state.version, dry_run=request.dry_run),
        operation,
        status=status,
        text="Owner contact",
    )
    if not request.is_edit:
        _print_contact(state.owner_contact)
    elif changed and not console.is_json_mode():
        console.ok(t("fleet_contact.changed"))
        console.info(t("fleet_contact.refresh", hours=state.update_interval_hours))


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
