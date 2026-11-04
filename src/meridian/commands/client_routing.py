"""Read or confirm a stateless service review of a person's private routing choice."""

from __future__ import annotations

import typer
from pydantic import ValidationError

from meridian import console
from meridian.commands._helpers import load_people_cluster as load_topology_cluster
from meridian.commands.client_handoff import resolve_client_mutation_name
from meridian.control.client import ControlClient, ControlServicePolicyError, ControlServiceUncertain
from meridian.control_access import access_panel
from meridian.core.errors import MeridianError
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.person_delivery_policy import (
    ClientRoutingResult,
    PersonDeliveryPolicyState,
    PersonDeliveryPolicyWrite,
)
from meridian.i18n import isolate, t
from meridian.renderers import emit_json


def run_routing(
    person: str, *, policy: str | None = None, inherit: bool = False, dry_run: bool = False, yes: bool = False
) -> None:
    operation = OperationContext()
    with console.error_context("client.routing", timer=operation.timer):
        if inherit and policy is not None:
            console.fail(t("delivery.choose_one"), hint_type="user")
        if dry_run and policy is None and not inherit:
            console.fail(t("delivery.choose"), hint_type="user")
        try:
            request = PersonDeliveryPolicyWrite(policy=policy) if policy is not None or inherit else None
        except ValidationError:
            console.fail(t("delivery.invalid"), hint_type="user")
        if request is not None and not dry_run and not yes and (console.is_json_mode() or not console.has_tty()):
            console.fail(t("delivery.requires_yes"), hint_type="user")
        cluster = load_topology_cluster()
        if not cluster.runs_control_service:
            console.fail(t("delivery.control_required"), hint_type="user")
        try:
            with access_panel(cluster) as panel:
                username = resolve_client_mutation_name(cluster, panel, person)
            with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
                state = client.person_delivery_policy(username)
                result = ClientRoutingResult(state=state)
                changed = False
                if request is not None and state.override != request.policy:
                    preview = client.set_person_delivery_policy(username, request)
                    if not console.is_json_mode():
                        console.info(
                            t(
                                "delivery.review",
                                old=isolate(preview.before.effective.policy.id),
                                new=isolate(preview.after.effective.policy.id),
                            )
                        )
                        console.info(t("delivery.refresh"))
                    if dry_run:
                        result = ClientRoutingResult(state=preview.after, review=preview, dry_run=True)
                    else:
                        if not yes and not console.confirm(
                            t("delivery.confirm", name=isolate(username)), default=False
                        ):
                            raise typer.Exit(1)
                        try:
                            applied = client.set_person_delivery_policy(
                                username,
                                PersonDeliveryPolicyWrite(
                                    policy=request.policy, confirm=True, review_hash=preview.review_hash
                                ),
                            )
                        except ControlServiceUncertain:
                            console.fail(t("delivery.uncertain", name=isolate(username)), hint_type="system")
                        result = ClientRoutingResult(state=applied.after, review=applied, applied=True)
                        changed = True
                elif dry_run:
                    result = ClientRoutingResult(state=state, dry_run=True)
        except ControlServicePolicyError as exc:
            console.fail(t(f"delivery.error_{exc.reason}"), hint_type="user")
        except MeridianError as exc:
            console.fail(exc)
        if console.is_json_mode():
            emit_json(
                command_envelope(
                    command="client.routing",
                    data=result.to_data(),
                    summary=Summary(text="Person client routing policy", changed=changed),
                    status="changed" if changed else "no_changes" if request is not None and not dry_run else "ok",
                    timer=operation.timer,
                )
            )
        else:
            print_policy(result.state)


def print_policy(state: PersonDeliveryPolicyState) -> None:
    console.info(
        t(
            "delivery.person",
            name=isolate(state.username),
            policy=isolate(state.effective.policy.id),
            source=t(f"delivery.source_{state.effective.source}"),
        )
    )
