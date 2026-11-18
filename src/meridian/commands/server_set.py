"""Review and update current server billing in the control service."""

from __future__ import annotations

from pydantic import ValidationError

from meridian import config, console
from meridian.commands._helpers import load_topology_cluster, require_intent
from meridian.commands._validation import validate_command_input
from meridian.commands.server_display import print_metadata
from meridian.core.errors import MeridianError
from meridian.core.models import OutputStatus, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.server_allowances import (
    ServerMetadataError,
    ServerSetRequest,
    ServerSetResult,
    build_server_metadata_change,
)
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
    # `fleet status` names servers by their role (`main`, `exit-2`), so a role ID finds its server too.
    roles = {exit_.id: exit_.server_ref for exit_ in intent.exits}
    roles |= {gateway.id: gateway.server_ref for gateway in intent.routing_gateways}
    roles |= {
        relay.id: relay.hop_server_refs[0] for relay in intent.transparent_relays if len(relay.hop_server_refs) == 1
    }
    if query in roles:
        return roles[query]
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
    from meridian.commands.fleet_presentation import live_presentation, presentation_client, review_presentation
    from meridian.core.presentation import PresentationPatch, PresentationState

    if request.dry_run and not request.change.model_fields_set:
        console.fail(t("server_set.choose_for_review"), hint_type="user")
    with presentation_client(cluster) as client:
        before = live_presentation(client)
        if ref not in before.servers:
            console.fail(
                t("server_set.unknown", name=request.query), hint=t("server_set.unknown_hint"), hint_type="user"
            )
        state, changed = before, False
        if request.change.model_fields_set:

            def show(old: PresentationState, new: PresentationState) -> None:
                console.info(t("fleet_contact.before"))
                print_metadata(old.servers[ref])
                console.info(t("fleet_contact.after"))
                print_metadata(new.servers[ref])

            state, changed = review_presentation(
                client,
                before,
                PresentationPatch(expected_version=before.version, servers={ref: request.change}),
                dry_run=request.dry_run,
                yes=request.yes,
                show=show,
            )
    metadata = state.servers[ref]
    status: OutputStatus = (
        "changed"
        if changed
        else "no_changes"
        if request.change.model_fields_set and metadata == before.servers[ref]
        else "ok"
    )
    _emit(
        ServerSetResult(server_ref=ref, metadata=metadata, version=state.version, dry_run=request.dry_run),
        operation,
        status,
        "Server allowance and payment settings",
    )
    if not request.change.model_fields_set:
        print_metadata(metadata)
    elif changed and not console.is_json_mode():
        console.ok(t("presentation.saved"))


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
