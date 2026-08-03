"""Typed command metadata independent of JSON Schema registration."""

from __future__ import annotations

from typing import Any, Literal

from meridian.core.models import CoreModel, ErrorCategory, OutputStatus

OutcomeCategory = ErrorCategory | Literal["none"]
_INTERRUPT_EXIT_MEANING = "process interrupted; no JSON envelope is emitted"


class CommandOutcome(CoreModel):
    """Structured command outcome for process clients."""

    status: OutputStatus
    exit_code: int
    category: OutcomeCategory
    meaning: str


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


def _standard_outcomes(success_status: Literal["ok"], success_meaning: str) -> list[CommandOutcome]:
    return [
        CommandOutcome(status=success_status, exit_code=0, category="none", meaning=success_meaning),
        CommandOutcome(status="failed", exit_code=2, category="user", meaning="user or configuration error"),
        CommandOutcome(status="failed", exit_code=3, category="system", meaning="system or infrastructure failure"),
        CommandOutcome(status="failed", exit_code=1, category="bug", meaning="unexpected Meridian bug"),
    ]


def _plan_outcomes() -> list[CommandOutcome]:
    return [
        CommandOutcome(status="no_changes", exit_code=0, category="none", meaning="desired state already matches"),
        CommandOutcome(status="changed", exit_code=2, category="none", meaning="changes pending"),
        CommandOutcome(status="failed", exit_code=2, category="user", meaning="user or configuration error"),
        CommandOutcome(status="failed", exit_code=3, category="system", meaning="system or infrastructure failure"),
        CommandOutcome(status="failed", exit_code=1, category="bug", meaning="unexpected Meridian bug"),
    ]


def _apply_outcomes() -> list[CommandOutcome]:
    return [
        CommandOutcome(status="no_changes", exit_code=0, category="none", meaning="desired state already matches"),
        CommandOutcome(status="changed", exit_code=0, category="none", meaning="changes were applied"),
        CommandOutcome(status="failed", exit_code=2, category="user", meaning="user or configuration error"),
        CommandOutcome(status="failed", exit_code=3, category="system", meaning="system or infrastructure failure"),
        CommandOutcome(status="failed", exit_code=1, category="bug", meaning="unexpected Meridian bug"),
    ]


def _deploy_outcomes() -> list[CommandOutcome]:
    return [
        CommandOutcome(status="changed", exit_code=0, category="none", meaning="server was deployed"),
        CommandOutcome(status="ok", exit_code=0, category="none", meaning="deploy request was validated or planned"),
        CommandOutcome(status="failed", exit_code=2, category="user", meaning="user or configuration error"),
        CommandOutcome(status="failed", exit_code=3, category="system", meaning="system or infrastructure failure"),
        CommandOutcome(status="failed", exit_code=1, category="bug", meaning="unexpected Meridian bug"),
    ]


def _verification_outcomes(subject: str) -> list[CommandOutcome]:
    return [
        CommandOutcome(status="ok", exit_code=0, category="none", meaning=f"{subject} completed and passed"),
        CommandOutcome(
            status="ok",
            exit_code=4,
            category="none",
            meaning=f"{subject} completed with negative findings",
        ),
        CommandOutcome(status="failed", exit_code=2, category="user", meaning="user or configuration error"),
        CommandOutcome(
            status="failed",
            exit_code=3,
            category="system",
            meaning=f"{subject} failed or was inconclusive",
        ),
        CommandOutcome(status="failed", exit_code=1, category="bug", meaning="unexpected Meridian bug"),
    ]


def _fleet_status_outcomes() -> list[CommandOutcome]:
    return [
        CommandOutcome(status="ok", exit_code=0, category="none", meaning="fleet health is healthy"),
        CommandOutcome(status="ok", exit_code=4, category="none", meaning="fleet health is degraded"),
        CommandOutcome(status="ok", exit_code=3, category="none", meaning="fleet health is unknown"),
        CommandOutcome(status="failed", exit_code=2, category="user", meaning="user or configuration error"),
        CommandOutcome(status="failed", exit_code=3, category="system", meaning="system or infrastructure failure"),
        CommandOutcome(status="failed", exit_code=1, category="bug", meaning="unexpected Meridian bug"),
    ]


def _client_add_outcomes() -> list[CommandOutcome]:
    return [
        CommandOutcome(status="ok", exit_code=0, category="none", meaning="all requested clients were added"),
        CommandOutcome(
            status="ok",
            exit_code=3,
            category="none",
            meaning="clients were added with failed requests or incomplete page/local-state follow-up",
        ),
        CommandOutcome(status="failed", exit_code=2, category="user", meaning="invalid input or client conflict"),
        CommandOutcome(status="failed", exit_code=3, category="system", meaning="system or panel failure"),
        CommandOutcome(status="failed", exit_code=1, category="bug", meaning="unexpected Meridian bug"),
    ]


def _client_show_outcomes() -> list[CommandOutcome]:
    return [
        CommandOutcome(status="ok", exit_code=0, category="none", meaning="client and available handoff were returned"),
        CommandOutcome(
            status="ok",
            exit_code=3,
            category="none",
            meaning="client was returned but page repair or its local evidence save was incomplete",
        ),
        CommandOutcome(status="failed", exit_code=2, category="user", meaning="client not found or input error"),
        CommandOutcome(status="failed", exit_code=3, category="system", meaning="system or panel failure"),
        CommandOutcome(status="failed", exit_code=1, category="bug", meaning="unexpected Meridian bug"),
    ]


def _client_remove_outcomes() -> list[CommandOutcome]:
    return [
        CommandOutcome(status="ok", exit_code=0, category="none", meaning="client was removed"),
        CommandOutcome(
            status="ok",
            exit_code=3,
            category="none",
            meaning="client was removed but local state or legacy page cleanup is incomplete",
        ),
        CommandOutcome(status="failed", exit_code=2, category="user", meaning="client not found or input error"),
        CommandOutcome(status="failed", exit_code=3, category="system", meaning="system or panel failure"),
        CommandOutcome(status="failed", exit_code=1, category="bug", meaning="unexpected Meridian bug"),
    ]


def _relay_list_outcomes() -> list[CommandOutcome]:
    return [
        CommandOutcome(status="ok", exit_code=0, category="none", meaning="relay list and host status were collected"),
        CommandOutcome(
            status="ok",
            exit_code=3,
            category="none",
            meaning="relay topology was listed but panel host status was unavailable",
        ),
        CommandOutcome(status="failed", exit_code=2, category="user", meaning="exit filter or configuration error"),
        CommandOutcome(status="failed", exit_code=3, category="system", meaning="system or panel failure"),
        CommandOutcome(status="failed", exit_code=1, category="bug", meaning="unexpected Meridian bug"),
    ]


def _node_list_outcomes() -> list[CommandOutcome]:
    return [
        CommandOutcome(status="ok", exit_code=0, category="none", meaning="node list and panel status were collected"),
        CommandOutcome(
            status="ok",
            exit_code=3,
            category="none",
            meaning="configured nodes were listed but live panel status was unavailable",
        ),
        CommandOutcome(status="failed", exit_code=2, category="user", meaning="configuration error"),
        CommandOutcome(status="failed", exit_code=3, category="system", meaning="system or panel failure"),
        CommandOutcome(status="failed", exit_code=1, category="bug", meaning="unexpected Meridian bug"),
    ]


def _fleet_inventory_outcomes() -> list[CommandOutcome]:
    return [
        CommandOutcome(status="ok", exit_code=0, category="none", meaning="inventory and live status were collected"),
        CommandOutcome(
            status="ok",
            exit_code=3,
            category="none",
            meaning="configured inventory was returned but live panel status was unavailable",
        ),
        CommandOutcome(status="failed", exit_code=2, category="user", meaning="configuration error"),
        CommandOutcome(status="failed", exit_code=3, category="system", meaning="system or infrastructure failure"),
        CommandOutcome(status="failed", exit_code=1, category="bug", meaning="unexpected Meridian bug"),
    ]


_COMMAND_CONTRACTS: dict[str, CommandContract] = {
    "apply": CommandContract(
        command="apply",
        argv=["apply"],
        envelope_schema="apply-envelope",
        data_schema="apply-command-data",
        failure_data_schema="apply-failure",
        error_schema="error",
        statuses=["no_changes", "changed", "failed"],
        outcomes=_apply_outcomes(),
        exit_codes={
            "0": "desired state was already converged or changes were applied",
            "1": "unexpected Meridian bug",
            "2": "user/config error",
            "3": "system or infrastructure failure",
            "130": _INTERRUPT_EXIT_MEANING,
        },
        machine_flags=["--json"],
        stability="preview",
        description="Converge desired state and emit typed action execution results.",
    ),
    "deploy": CommandContract(
        command="deploy",
        argv=["deploy"],
        envelope_schema="deploy-envelope",
        data_schema="deploy-command-data",
        failure_data_schema="empty-data",
        error_schema="error",
        statuses=["changed", "ok", "failed"],
        outcomes=_deploy_outcomes(),
        exit_codes={
            "0": "server was deployed or deploy request was validated/planned",
            "1": "unexpected Meridian bug",
            "2": "user/config error",
            "3": "system or infrastructure failure",
            "130": _INTERRUPT_EXIT_MEANING,
        },
        machine_flags=["--json", "--events=jsonl", "--request", "--dry-run"],
        stability="preview",
        description="Deploy or validate a Meridian server from a typed deploy request.",
    ),
    "api.commands": CommandContract(
        command="api.commands",
        argv=["api", "commands"],
        envelope_schema="api-commands-envelope",
        data_schema="api-commands",
        failure_data_schema="empty-data",
        error_schema="error",
        statuses=["ok", "failed"],
        outcomes=_standard_outcomes("ok", "command contracts were listed"),
        exit_codes={
            "0": "command contracts were listed",
            "1": "unexpected Meridian bug",
            "2": "user/config error",
            "3": "system or infrastructure failure",
            "130": _INTERRUPT_EXIT_MEANING,
        },
        machine_flags=["--json", "--include-schemas"],
        stability="stable",
        description=(
            "List migrated command contracts; with --include-schemas, embed envelope, data, error, and failure schemas."
        ),
    ),
    "api.schema": CommandContract(
        command="api.schema",
        argv=["api", "schema", "NAME"],
        envelope_schema="api-schema-envelope",
        data_schema="api-schema",
        failure_data_schema="empty-data",
        error_schema="error",
        statuses=["ok", "failed"],
        outcomes=_standard_outcomes("ok", "schema was found"),
        exit_codes={
            "0": "schema was found",
            "1": "unexpected Meridian bug",
            "2": "schema name is unknown",
            "3": "system or infrastructure failure",
            "130": _INTERRUPT_EXIT_MEANING,
        },
        machine_flags=["--envelope", "--json"],
        stability="stable",
        description="Return one JSON Schema. Without --envelope/global --json, success remains raw schema JSON.",
    ),
    "api.schemas": CommandContract(
        command="api.schemas",
        argv=["api", "schemas"],
        envelope_schema="api-schemas-envelope",
        data_schema="api-schemas",
        failure_data_schema="empty-data",
        error_schema="error",
        statuses=["ok", "failed"],
        outcomes=_standard_outcomes("ok", "schema catalog was listed"),
        exit_codes={
            "0": "schema catalog was listed",
            "1": "unexpected Meridian bug",
            "2": "user/config error",
            "3": "system or infrastructure failure",
            "130": _INTERRUPT_EXIT_MEANING,
        },
        machine_flags=["--json", "--include-schemas"],
        stability="stable",
        description="List meridian-core JSON Schema names; with --include-schemas, embed full schemas.",
    ),
    "api.workflow": CommandContract(
        command="api.workflow",
        argv=["api", "workflow", "NAME"],
        envelope_schema="api-workflow-envelope",
        data_schema="api-workflow",
        failure_data_schema="empty-data",
        error_schema="error",
        statuses=["ok", "failed"],
        outcomes=_standard_outcomes("ok", "workflow contract was returned"),
        exit_codes={
            "0": "workflow contract was returned",
            "1": "unexpected Meridian bug",
            "2": "workflow name is unknown",
            "3": "system or infrastructure failure",
            "130": _INTERRUPT_EXIT_MEANING,
        },
        machine_flags=["--json"],
        stability="preview",
        description="Return a UI-renderable workflow plan such as the deploy wizard field layout.",
    ),
    "client.list": CommandContract(
        command="client.list",
        argv=["client", "list"],
        envelope_schema="client-list-envelope",
        data_schema="client-list",
        failure_data_schema="empty-data",
        error_schema="error",
        statuses=["ok", "failed"],
        outcomes=_standard_outcomes("ok", "client list was collected"),
        exit_codes={
            "0": "client list was collected",
            "1": "unexpected Meridian bug",
            "2": "user/config error",
            "3": "system or infrastructure failure",
            "130": _INTERRUPT_EXIT_MEANING,
        },
        machine_flags=["--json"],
        stability="stable",
        description="List managed access clients as redacted metadata plus aggregate status counts.",
    ),
    "client.add": CommandContract(
        command="client.add",
        argv=["client", "add", "NAME..."],
        envelope_schema="client-add-envelope",
        data_schema="client-add",
        failure_data_schema="empty-data",
        error_schema="error",
        statuses=["ok", "failed"],
        outcomes=_client_add_outcomes(),
        exit_codes={
            "0": "all requested clients were added",
            "1": "unexpected Meridian bug",
            "2": "invalid input or client already exists",
            "3": "partial batch or incomplete page/local-state follow-up, or system/panel failure",
            "130": _INTERRUPT_EXIT_MEANING,
        },
        machine_flags=["--json"],
        stability="stable",
        description="Add one or more panel clients and return their public identities.",
    ),
    "client.disable": CommandContract(
        command="client.disable",
        argv=["client", "disable", "NAME"],
        envelope_schema="client-disable-envelope",
        data_schema="client-status",
        failure_data_schema="empty-data",
        error_schema="error",
        statuses=["ok", "failed"],
        outcomes=_standard_outcomes("ok", "client was disabled"),
        exit_codes={
            "0": "client was disabled",
            "1": "unexpected Meridian bug",
            "2": "client not found or input/config error",
            "3": "system or panel failure",
            "130": _INTERRUPT_EXIT_MEANING,
        },
        machine_flags=["--json"],
        stability="stable",
        description="Disable one managed client; V4 suspension remains temporary until the next apply.",
    ),
    "client.enable": CommandContract(
        command="client.enable",
        argv=["client", "enable", "NAME"],
        envelope_schema="client-enable-envelope",
        data_schema="client-status",
        failure_data_schema="empty-data",
        error_schema="error",
        statuses=["ok", "failed"],
        outcomes=_standard_outcomes("ok", "client was enabled"),
        exit_codes={
            "0": "client was enabled",
            "1": "unexpected Meridian bug",
            "2": "client not found or input/config error",
            "3": "system or panel failure",
            "130": _INTERRUPT_EXIT_MEANING,
        },
        machine_flags=["--json"],
        stability="stable",
        description="Enable one panel client and return its resulting status.",
    ),
    "client.remove": CommandContract(
        command="client.remove",
        argv=["client", "remove", "NAME"],
        envelope_schema="client-remove-envelope",
        data_schema="client-remove",
        failure_data_schema="empty-data",
        error_schema="error",
        statuses=["ok", "failed"],
        outcomes=_client_remove_outcomes(),
        exit_codes={
            "0": "client was removed",
            "1": "unexpected Meridian bug",
            "2": "client not found or input/config error",
            "3": "client removed with incomplete local/page cleanup, or system/panel failure",
            "130": _INTERRUPT_EXIT_MEANING,
        },
        machine_flags=["--json", "--yes"],
        stability="stable",
        description="Remove one legacy client; V4 managed-user retirement is not yet supported.",
    ),
    "client.show": CommandContract(
        command="client.show",
        argv=["client", "show", "NAME"],
        envelope_schema="client-show-envelope",
        data_schema="client-show",
        failure_data_schema="empty-data",
        error_schema="error",
        statuses=["ok", "failed"],
        outcomes=_client_show_outcomes(),
        exit_codes={
            "0": "client was found",
            "1": "unexpected Meridian bug",
            "2": "client not found or input/config error",
            "3": "requested page repair or its local evidence save was incomplete, or a system/infrastructure failure",
            "130": _INTERRUPT_EXIT_MEANING,
        },
        machine_flags=["--json", "--repair-page"],
        stability="stable",
        description="Return one managed access client and evidenced, redacted handoff links.",
    ),
    "node.list": CommandContract(
        command="node.list",
        argv=["node", "list"],
        envelope_schema="node-list-envelope",
        data_schema="node-list",
        failure_data_schema="empty-data",
        error_schema="error",
        statuses=["ok", "failed"],
        outcomes=_node_list_outcomes(),
        exit_codes={
            "0": "node list was collected",
            "1": "unexpected Meridian bug",
            "2": "user or configuration error",
            "3": "system or panel failure",
            "130": _INTERRUPT_EXIT_MEANING,
        },
        machine_flags=["--json"],
        stability="stable",
        description="List configured nodes with live panel status when available.",
    ),
    "probe": CommandContract(
        command="probe",
        argv=["probe", "TARGET"],
        envelope_schema="probe-envelope",
        data_schema="probe-result",
        failure_data_schema="probe-failure",
        error_schema="error",
        statuses=["ok", "failed"],
        outcomes=_verification_outcomes("probe"),
        exit_codes={
            "0": "probe completed and every check passed",
            "1": "unexpected Meridian bug",
            "2": "user/config error",
            "3": "system or infrastructure failure, or verification was inconclusive",
            "4": "probe completed with negative findings",
            "130": _INTERRUPT_EXIT_MEANING,
        },
        machine_flags=["--json", "--server", "--sni", "--timeout"],
        stability="stable",
        description="Inspect a target and return typed fingerprint, exposure, and policy findings.",
    ),
    "relay.list": CommandContract(
        command="relay.list",
        argv=["relay", "list"],
        envelope_schema="relay-list-envelope",
        data_schema="relay-list",
        failure_data_schema="empty-data",
        error_schema="error",
        statuses=["ok", "failed"],
        outcomes=_relay_list_outcomes(),
        exit_codes={
            "0": "relay list was collected",
            "1": "unexpected Meridian bug",
            "2": "exit filter or configuration error",
            "3": "panel host status was unavailable, or a system/panel failure occurred",
            "130": _INTERRUPT_EXIT_MEANING,
        },
        machine_flags=["--json", "--exit"],
        stability="stable",
        description="List configured relays with live panel host status when available.",
    ),
    "plan": CommandContract(
        command="plan",
        argv=["plan"],
        envelope_schema="plan-envelope",
        data_schema="plan-command-data",
        failure_data_schema="plan-failure",
        error_schema="error",
        statuses=["no_changes", "changed", "failed"],
        outcomes=_plan_outcomes(),
        exit_codes={
            "0": "desired state already matches actual state",
            "1": "unexpected Meridian bug",
            "2": "changes pending; user/config errors also use category=user in the error envelope",
            "3": "system or infrastructure failure",
            "130": _INTERRUPT_EXIT_MEANING,
        },
        machine_flags=["--json"],
        stability="preview",
        description="Compute the desired-state reconciliation plan without applying it.",
    ),
    "fleet.status": CommandContract(
        command="fleet.status",
        argv=["fleet", "status"],
        envelope_schema="fleet-status-envelope",
        data_schema="fleet-status",
        failure_data_schema="empty-data",
        error_schema="error",
        statuses=["ok", "failed"],
        outcomes=_fleet_status_outcomes(),
        exit_codes={
            "0": "fleet status was collected and health is healthy",
            "1": "unexpected Meridian bug",
            "2": "user/config error",
            "3": "health is unknown, or a system/infrastructure failure prevented collection",
            "4": "fleet status was collected and health is degraded",
            "130": _INTERRUPT_EXIT_MEANING,
        },
        machine_flags=["--json"],
        stability="stable",
        description="Collect panel, node, relay, and user health observations for the configured fleet.",
    ),
    "fleet.inventory": CommandContract(
        command="fleet.inventory",
        argv=["fleet", "inventory"],
        envelope_schema="fleet-inventory-envelope",
        data_schema="fleet-inventory",
        failure_data_schema="empty-data",
        error_schema="error",
        statuses=["ok", "failed"],
        outcomes=_fleet_inventory_outcomes(),
        exit_codes={
            "0": "inventory was collected; plan --json is the drift/apply authority",
            "1": "unexpected Meridian bug",
            "2": "user/config error",
            "3": "live panel evidence unavailable, or a system/infrastructure failure",
            "130": _INTERRUPT_EXIT_MEANING,
        },
        machine_flags=["--json"],
        stability="stable",
        description="Return the configured fleet topology plus live panel observations when available.",
    ),
    "test": CommandContract(
        command="test",
        argv=["test", "TARGET"],
        envelope_schema="test-envelope",
        data_schema="test-result",
        failure_data_schema="test-failure",
        error_schema="error",
        statuses=["ok", "failed"],
        outcomes=_verification_outcomes("test"),
        exit_codes={
            "0": "test completed and every check passed",
            "1": "unexpected Meridian bug",
            "2": "user/config error",
            "3": "system or infrastructure failure, or verification was inconclusive",
            "4": "test completed with negative findings",
            "130": _INTERRUPT_EXIT_MEANING,
        },
        machine_flags=[
            "--json",
            "--server",
            "--domain",
            "--sni",
            "--client",
            "--basic",
            "--timeout",
        ],
        stability="stable",
        description="Verify delivered proxy traffic end to end and return evidence-aware protocol findings.",
    ),
}


def command_contract(command: str) -> CommandContract | None:
    """Return one command contract, if the command is part of the process API."""
    return _COMMAND_CONTRACTS.get(command)


def command_contracts() -> list[CommandContract]:
    """Return command contracts in stable command-name order."""
    return [_COMMAND_CONTRACTS[name] for name in sorted(_COMMAND_CONTRACTS)]


def command_schema_bindings() -> dict[str, str]:
    """Map command names to their advertised envelope schemas."""
    return {command: contract.envelope_schema for command, contract in _COMMAND_CONTRACTS.items()}
