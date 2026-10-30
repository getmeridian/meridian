"""Read or change the fleet's reviewed client routing policy."""

from __future__ import annotations

import typer
from rich.markup import escape

from meridian import config, console
from meridian.commands._helpers import apply_reviewed_intent, load_topology_cluster, require_intent, reviewed_runtime
from meridian.commands._validation import validate_command_input
from meridian.core.apply import CompiledApplyPreview, build_compiled_apply_result
from meridian.core.client_routing import FleetRoutingRequest, FleetRoutingResult
from meridian.core.errors import MeridianError
from meridian.core.models import OutputStatus, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.setup import SetupDraft
from meridian.core.topology import SetupIntent
from meridian.i18n import t
from meridian.renderers import emit_json
from meridian.servers import ServerRegistry

COMMAND = "fleet.routing"


def run_routing(policy: str | None = None, *, dry_run: bool = False, yes: bool = False) -> None:
    """Read local intent, or confirm and apply exactly the reviewed policy edit."""
    operation = OperationContext()
    with console.error_context(COMMAND, timer=operation.timer):
        request = validate_command_input(
            FleetRoutingRequest, t("fleet_routing.invalid"), client_routing=policy, dry_run=dry_run, yes=yes
        )
        _run(request, operation)


def _run(request: FleetRoutingRequest, operation: OperationContext) -> None:
    if request.client_routing is None and request.dry_run:
        console.fail(t("fleet_routing.choose_for_review"), hint_type="user")
    cluster = load_topology_cluster()
    current = require_intent(cluster)
    policy = request.client_routing or current.delivery.client_routing
    result = FleetRoutingResult(client_routing=policy, dry_run=request.dry_run)
    if request.client_routing is None:
        _emit(result, operation, status="ok", text="Read client routing policy")
        _print_policy(result)
        return
    if policy == current.delivery.client_routing:
        _emit(result, operation, status="no_changes", text="Client routing is unchanged")
        if not console.is_json_mode():
            console.ok(t("fleet_routing.unchanged", policy=policy))
        _print_policy(result)
        return
    if not request.dry_run and not request.yes and (console.is_json_mode() or not console.has_tty()):
        console.fail(
            t("fleet_routing.needs_yes"), hint=t("fleet_routing.needs_yes_hint", policy=policy), hint_type="user"
        )

    data = current.model_dump(mode="json")
    data["delivery"]["client_routing"] = policy
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
    result = FleetRoutingResult(client_routing=policy, dry_run=request.dry_run, review=preview)
    if not console.is_json_mode():
        console.err_console.print()
        console.info(t("fleet_routing.review_title", old=current.delivery.client_routing, new=policy))
        _print_policy(result)
        console.info(t("fleet_routing.refresh", hours=updated.delivery.update_interval_hours))
        if preview.retiring_resources:
            console.info(t("fleet_routing.retiring", count=len(preview.retiring_resources)))
            for logical_id in preview.retiring_resources:
                console.err_console.print(f"    [red]remove[/red] {escape(logical_id)}")
        console.err_console.print(f"  [dim]Review {preview.review_hash}[/dim]")
    if request.dry_run:
        _emit(result, operation, status="ok", text="Reviewed client routing policy")
        if not console.is_json_mode():
            console.info(t("fleet_routing.dry_run", policy=policy))
        return
    if not request.yes and not console.confirm(t("fleet_routing.confirm", policy=policy), default=False):
        raise typer.Exit(1)

    execution = apply_reviewed_intent(
        cluster,
        registry,
        updated,
        failure=t("fleet_routing.failed"),
        hint=t("fleet_routing.failed_hint"),
        expected_plan_hash=review.review_hash,
    )
    applied = build_compiled_apply_result(execution, exit_code=0, summary="Topology converged.")
    _emit(
        FleetRoutingResult(client_routing=policy, review=preview, applied=applied),
        operation,
        status="changed",
        text="Applied client routing policy",
    )
    if not console.is_json_mode():
        console.ok(t("fleet_routing.changed", policy=policy))
        console.info(t("fleet_routing.refresh", hours=updated.delivery.update_interval_hours))


def _print_policy(result: FleetRoutingResult) -> None:
    if console.is_json_mode():
        return
    console.info(t("fleet_routing.current", policy=result.client_routing))
    for policy, description in (("ru-split", t("fleet_routing.ru_split")), ("full", t("fleet_routing.full"))):
        console.err_console.print(f"  [bold]{policy}[/bold] — {escape(description)}")
    for note in (t("fleet_routing.bittorrent"), t("fleet_routing.quic"), t("fleet_routing.base64")):
        console.err_console.print(f"  [dim]{escape(note)}[/dim]")
    if result.client_routing == "full":
        console.err_console.print(f"  [dim]{escape(t('fleet_routing.exit_policy'))}[/dim]")


def _emit(result: FleetRoutingResult, operation: OperationContext, *, status: OutputStatus, text: str) -> None:
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
