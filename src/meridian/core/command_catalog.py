"""What each CLI command emits for process clients, independent of JSON Schema registration.

The CLI tree owns a command's arguments, options, and help; this module owns only
what the tree cannot know: whether a command emits a JSON envelope, with which data
schemas, outcomes, and stability. `meridian.cli_catalog` joins the two.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from meridian.core.models import CoreModel, ErrorCategory, OutputStatus

OutcomeCategory = ErrorCategory | Literal["none"]
INTERRUPT_EXIT_MEANING = "process interrupted; no JSON envelope is emitted"


class CommandOutcome(CoreModel):
    """Structured command outcome for process clients."""

    status: OutputStatus
    exit_code: int
    category: OutcomeCategory
    meaning: str


class CommandOutput(CoreModel):
    """The JSON envelope one command emits: data schemas, completed outcomes, stability."""

    data_schema: str
    failure_data_schema: str = "empty-data"
    outcomes: list[CommandOutcome]
    stability: Literal["stable", "preview"]


class CommandContract(CoreModel):
    """Discoverable command-to-schema contract for process API clients."""

    command: str
    argv: list[str]
    envelope_schema: str
    data_schema: str
    failure_data_schema: str
    error_schema: str
    statuses: list[OutputStatus]
    outcomes: list[CommandOutcome]
    exit_codes: dict[str, str]
    machine_flags: list[str]
    interrupt_behavior: Literal["exit_130_without_envelope"] = "exit_130_without_envelope"
    stability: Literal["stable", "preview"]
    description: str


class CommandCatalogEntry(CommandContract):
    """Command contract entry with optional embedded schemas."""

    envelope: dict[str, Any] | None = None
    data: dict[str, Any] | None = None
    failure_data: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


def _ok(meaning: str, *, exit_code: int = 0, status: OutputStatus = "ok") -> CommandOutcome:
    return CommandOutcome(status=status, exit_code=exit_code, category="none", meaning=meaning)


def _user(meaning: str = "user or configuration error", *, exit_code: int = 2) -> CommandOutcome:
    return CommandOutcome(status="failed", exit_code=exit_code, category="user", meaning=meaning)


def _system(meaning: str = "system or infrastructure failure") -> CommandOutcome:
    return CommandOutcome(status="failed", exit_code=3, category="system", meaning=meaning)


_BUG = CommandOutcome(status="failed", exit_code=1, category="bug", meaning="unexpected Meridian bug")
_FAILURES = [_user(), _system(), _BUG]
_PANEL_FAILURES = [_system("system or panel failure"), _BUG]


def _verification(subject: str) -> list[CommandOutcome]:
    return [
        _ok(f"{subject} completed and passed"),
        _ok(f"{subject} completed with negative findings", exit_code=4),
        _user(),
        _system(f"{subject} failed or was inconclusive"),
        _BUG,
    ]


def _route_mutation(success: str) -> list[CommandOutcome]:
    return [
        _ok(success),
        _user("unknown exit or route, or invalid values"),
        _system("apply or local-state failure"),
        _BUG,
    ]


# Every public command, keyed by its dotted path; None means human output only.
COMMAND_OUTPUTS: dict[str, CommandOutput | None] = {
    "setup": None,
    "deploy": CommandOutput(
        data_schema="deploy-command-data",
        outcomes=[
            _ok("server was deployed", status="changed"),
            _ok("deploy request was validated or planned"),
            CommandOutcome(
                status="cancelled",
                exit_code=5,
                category="cancelled",
                meaning="deploy stopped at a safe point after a cancel request; re-run resumes",
            ),
            *_FAILURES,
        ],
        stability="preview",
    ),
    "help": None,
    "client.add": CommandOutput(
        data_schema="client-add",
        outcomes=[
            _ok("all requested clients were added"),
            _ok("clients were added with failed requests or incomplete page/local-state follow-up", exit_code=3),
            _user("invalid input or client conflict"),
            *_PANEL_FAILURES,
        ],
        stability="stable",
    ),
    "client.show": CommandOutput(
        data_schema="client-show",
        outcomes=[
            _ok("client and available handoff were returned"),
            _ok("client was returned but page repair or its local evidence save was incomplete", exit_code=3),
            _user("client not found or input error"),
            *_PANEL_FAILURES,
        ],
        stability="stable",
    ),
    "client.list": CommandOutput(
        data_schema="client-list", outcomes=[_ok("client list was collected"), *_FAILURES], stability="stable"
    ),
    "client.remove": CommandOutput(
        data_schema="client-remove",
        outcomes=[
            _ok("client was removed"),
            _ok("client was removed but their connection page could not be deleted", exit_code=3),
            _user("client not found or input error"),
            *_PANEL_FAILURES,
        ],
        stability="stable",
    ),
    "client.enable": CommandOutput(
        data_schema="client-status",
        outcomes=[_ok("client was enabled"), _user("client not found or input error"), *_PANEL_FAILURES],
        stability="stable",
    ),
    "client.disable": CommandOutput(
        data_schema="client-status",
        outcomes=[_ok("client was disabled"), _user("client not found or input error"), *_PANEL_FAILURES],
        stability="stable",
    ),
    "node.add": None,
    "node.list": CommandOutput(
        data_schema="node-list",
        outcomes=[
            _ok("node list and panel status were collected"),
            _ok("configured nodes were listed but live panel status was unavailable", exit_code=3),
            _user("configuration error"),
            *_PANEL_FAILURES,
        ],
        stability="stable",
    ),
    "node.replace": None,
    "node.remove": None,
    "relay.add": None,
    "relay.list": CommandOutput(
        data_schema="relay-list",
        outcomes=[
            _ok("relay list and host status were collected"),
            _ok("relay topology was listed but panel host status was unavailable", exit_code=3),
            _user("exit filter or configuration error"),
            *_PANEL_FAILURES,
        ],
        stability="stable",
    ),
    "relay.remove": None,
    "route.add": CommandOutput(
        data_schema="route-mutation",
        outcomes=_route_mutation("the override was added and applied"),
        stability="preview",
    ),
    "route.list": CommandOutput(
        data_schema="route-list",
        outcomes=[_ok("overrides were listed"), _user("unknown exit or configuration error"), _BUG],
        stability="preview",
    ),
    "route.remove": CommandOutput(
        data_schema="route-mutation",
        outcomes=_route_mutation("the override was removed and applied"),
        stability="preview",
    ),
    "fleet.status": CommandOutput(
        data_schema="fleet-status",
        outcomes=[
            _ok("fleet health is healthy"),
            _ok("fleet health is degraded", exit_code=4),
            _ok("fleet health is unknown", exit_code=3),
            *_FAILURES,
        ],
        stability="stable",
    ),
    "fleet.inventory": CommandOutput(
        data_schema="fleet-inventory",
        outcomes=[
            _ok("inventory and live status were collected"),
            _ok("configured inventory was returned but live panel status was unavailable", exit_code=3),
            _user("configuration error"),
            _system(),
            _BUG,
        ],
        stability="stable",
    ),
    "fleet.upgrade": None,
    "server.add": None,
    "server.list": None,
    "server.remove": None,
    "check": CommandOutput(
        data_schema="check", failure_data_schema="check-failure", outcomes=_verification("check"), stability="stable"
    ),
    "test": CommandOutput(
        data_schema="test-result",
        failure_data_schema="test-failure",
        outcomes=_verification("test"),
        stability="stable",
    ),
    "probe": CommandOutput(
        data_schema="probe-result",
        failure_data_schema="probe-failure",
        outcomes=_verification("probe"),
        stability="stable",
    ),
    "doctor": None,
    "logs": None,
    "plan": CommandOutput(
        data_schema="plan-command-data",
        failure_data_schema="plan-failure",
        outcomes=[
            _ok("desired state already matches", status="no_changes"),
            _ok("changes pending", exit_code=2, status="changed"),
            _user(exit_code=1),
            _system(),
            _BUG,
        ],
        stability="preview",
    ),
    "apply": CommandOutput(
        data_schema="apply-command-data",
        failure_data_schema="apply-failure",
        outcomes=[
            _ok("desired state already matches", status="no_changes"),
            _ok("changes were applied", status="changed"),
            *_FAILURES,
        ],
        stability="preview",
    ),
    "panel": None,
    "scan": None,
    "studio": None,
    "update": None,
    "teardown": None,
    "api.schemas": CommandOutput(
        data_schema="api-schemas", outcomes=[_ok("schema catalog was listed"), *_FAILURES], stability="stable"
    ),
    "api.commands": CommandOutput(
        data_schema="api-commands", outcomes=[_ok("command contracts were listed"), *_FAILURES], stability="stable"
    ),
    "api.schema": CommandOutput(
        data_schema="api-schema",
        outcomes=[_ok("schema was found"), _user("schema name is unknown"), _system(), _BUG],
        stability="stable",
    ),
    "api.workflow": CommandOutput(
        data_schema="api-workflow",
        outcomes=[_ok("workflow contract was returned"), _user("workflow name is unknown"), _system(), _BUG],
        stability="preview",
    ),
}


def command_output(command: str) -> CommandOutput | None:
    """Return what one command emits as JSON, or None for human-only and unknown commands."""
    return COMMAND_OUTPUTS.get(command)


def envelope_schema(command: str) -> str:
    """Name of the envelope schema a JSON command emits."""
    return f"{command.replace('.', '-')}-envelope"


def command_schema_bindings() -> dict[str, str]:
    """Map JSON command names to their advertised envelope schemas."""
    return {command: envelope_schema(command) for command, output in COMMAND_OUTPUTS.items() if output is not None}


def _exit_codes(outcomes: Iterable[CommandOutcome]) -> dict[str, str]:
    meanings: dict[int, list[str]] = {}
    for outcome in outcomes:
        if outcome.meaning not in meanings.setdefault(outcome.exit_code, []):
            meanings[outcome.exit_code].append(outcome.meaning)
    meanings[130] = [INTERRUPT_EXIT_MEANING]
    return {str(code): "; ".join(meanings[code]) for code in sorted(meanings)}


def command_contract(
    command: str, output: CommandOutput, *, argv: list[str], machine_flags: list[str], description: str
) -> CommandContract:
    """Join a command's output contract with the argv, flags, and help its CLI declares."""
    statuses: list[OutputStatus] = []
    for outcome in output.outcomes:
        if outcome.status not in statuses:
            statuses.append(outcome.status)
    return CommandContract(
        command=command,
        argv=argv,
        envelope_schema=envelope_schema(command),
        data_schema=output.data_schema,
        failure_data_schema=output.failure_data_schema,
        error_schema="error",
        statuses=statuses,
        outcomes=output.outcomes,
        exit_codes=_exit_codes(output.outcomes),
        machine_flags=machine_flags,
        stability=output.stability,
        description=description,
    )
