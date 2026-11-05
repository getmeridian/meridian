"""A CLI adapter for the same policy explanation the console and API request."""

from __future__ import annotations

from pydantic import ValidationError

from meridian import console
from meridian.commands._helpers import load_topology_cluster
from meridian.commands.client_handoff import resolve_client_mutation_name
from meridian.control.client import ControlClient
from meridian.control_access import access_panel
from meridian.core.delivery_explain import DeliveryExplainRequest, DeliveryExplanation
from meridian.core.errors import MeridianError
from meridian.core.output import OperationContext, command_envelope
from meridian.i18n import isolate, t
from meridian.renderers import emit_json


def run_explain(
    destination: str, *, person: str | None, document: str, port: int | None, network: str | None, protocol: str
) -> None:
    operation = OperationContext()
    with console.error_context("fleet.routing.explain", timer=operation.timer):
        try:
            request = DeliveryExplainRequest.model_validate(
                {
                    "destination": destination,
                    "person": person,
                    "format": document,
                    "port": port,
                    "network": network,
                    "protocol": protocol,
                }
            )
        except ValidationError:
            console.fail(t("policy.invalid_destination"), hint_type="user")
        cluster = load_topology_cluster()
        if not cluster.runs_control_service:
            console.fail(t("delivery.control_required"), hint_type="user")
        try:
            if person is not None:
                with access_panel(cluster) as panel:
                    person = resolve_client_mutation_name(cluster, panel, person)
                request = DeliveryExplainRequest.model_validate({**request.to_data(), "person": person})
            with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
                result = client.explain_delivery_policy(request)
        except MeridianError as exc:
            console.fail(exc)
        if console.is_json_mode():
            emit_json(
                command_envelope(
                    command="fleet.routing.explain",
                    data=result.to_data(),
                    summary="Client destination explanation",
                    timer=operation.timer,
                )
            )
        else:
            print_explanation(result)


def print_explanation(result: DeliveryExplanation) -> None:
    destination = result.destination.host
    if result.destination.port is not None:
        destination += ":" + str(result.destination.port)
    if result.action is not None:
        action = (
            t("policy.action_exit", exit=isolate(result.action.exit or ""))
            if result.action.kind == "exit"
            else t(f"policy.action_{result.action.kind}")
        )
        console.info(
            t("policy.explained_rule", destination=isolate(destination), action=action, rule=result.rule)
            if result.rule is not None
            else t("policy.explained_default", destination=isolate(destination), action=action)
        )
    else:
        console.info(t(f"policy.explain_{result.reason}"))
    console.info(t("policy.no_lookup"))
