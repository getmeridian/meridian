"""Read and explain the service's applied app rules without storing the synthetic request."""

from __future__ import annotations

from pydantic import ValidationError
from rich.markup import escape

from meridian import console
from meridian.commands._helpers import load_topology_cluster
from meridian.commands.client_handoff import resolve_client_mutation_name
from meridian.control.client import ControlClient
from meridian.control_access import access_panel
from meridian.core.errors import MeridianError
from meridian.core.output import OperationContext, command_envelope
from meridian.core.response_rule_test import ResponseRuleDecision, ResponseRuleTestRequest
from meridian.i18n import isolate, t
from meridian.renderers import emit_json

COMMAND = "fleet.response-rules.test"


def run_test(*, user_agent: str, accept: str, person: str | None, suffix: str) -> None:
    operation = OperationContext()
    with console.error_context(COMMAND, timer=operation.timer):
        try:
            request = ResponseRuleTestRequest(user_agent=user_agent, accept=accept, person=person, suffix=suffix)
        except ValidationError as exc:
            field = str(exc.errors()[0]["loc"][0])
            console.fail(t(f"preview.invalid_{field}"), hint_type="user")
        cluster = load_topology_cluster()
        if not cluster.runs_control_service:
            console.fail(t("delivery.control_required"), hint_type="user")
        try:
            if person is not None:
                with access_panel(cluster) as panel:
                    person = resolve_client_mutation_name(cluster, panel, person)
                request = ResponseRuleTestRequest(**{**request.to_data(), "person": person})
            with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
                result = client.test_response_rules(request)
        except MeridianError as exc:
            console.fail(exc)
        if console.is_json_mode():
            emit_json(
                command_envelope(
                    command=COMMAND, data=result.to_data(), summary="App response rule test", timer=operation.timer
                )
            )
        else:
            print_response_rule_decision(result)


def print_response_rule_decision(result: ResponseRuleDecision) -> None:
    console.info(t("response_rules.title"))
    if result.selection.name is not None:
        console.info(t("response_rules.custom", number=result.selection.number, name=isolate(result.selection.name)))
    console.info(t(f"response_rules.reason_{result.reason}"))
    for condition in result.matched_conditions:
        console.info(
            t(
                "response_rules.condition",
                header=condition.header,
                operator=t(f"response_rules.operator_{condition.operator}"),
            )
        )
    console.info(t("response_rules.blocked") if result.block else t(f"preview.format_{result.format}"))
    if result.block:
        console.info(t("response_rules.refusal"))
    if result.fallback is not None:
        console.info(t("response_rules.fallback", rule=t(f"preview.rule_{result.fallback.rule}")))
    if result.policy is not None:
        console.info(
            t(
                "response_rules.policy",
                policy=isolate(result.policy.policy.id),
                source=t(f"delivery.source_{result.policy.source}"),
            )
        )
    if result.headers:
        console.info(t("preview.headers"))
        for name, value in result.headers.items():
            console.err_console.print("  " + escape(name + ": " + value))
