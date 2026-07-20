"""JSON Schema catalog for meridian-core contracts."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, RootModel, create_model

from meridian.core.apply import (
    ApplyActionResult,
    ApplyCommandData,
    ApplyCounts,
    ApplyResult,
    CompiledApplyActionResult,
    CompiledApplyPreview,
    CompiledApplyResult,
)
from meridian.core.clients import ClientListResult, ClientShowResult
from meridian.core.command_inputs import (
    ClientNameRequest,
    NodeAddRequest,
    NodeTargetRequest,
    RelayDeployRequest,
    RelayTargetRequest,
    ServerAddRequest,
    ServerRemoveRequest,
)
from meridian.core.deploy import DeployRequest, DeployResult, DeployWorkflowAnswers
from meridian.core.deploy_planning import DeployClusterState, DeployNodeState, DeployPlan, DeployPorts
from meridian.core.events import EVENT_TYPES
from meridian.core.execution import CommandSpec, PutBytesSpec, PutTextSpec, RemoteCommandResult, RemoteTarget
from meridian.core.fleet import FleetInventory, FleetStatus
from meridian.core.models import (
    CoreModel,
    ErrorCategory,
    Event,
    MeridianError,
    OutputEnvelope,
    OutputSchema,
    OutputStatus,
    Summary,
)
from meridian.core.operations import (
    DeployOperationResult,
    OperationCancel,
    OperationDiagnostics,
    OperationEnvelope,
    OperationError,
    OperationEvents,
    OperationList,
    OperationResultEnvelope,
    OperationSnapshot,
    OperationStart,
)
from meridian.core.plan import (
    CompiledPlanDriftResult,
    CompiledPlanResourceResult,
    CompiledPlanResult,
    PlanActionResult,
    PlanCommandData,
    PlanCounts,
    PlanResult,
)
from meridian.core.schema_topology import TOPOLOGY_SCHEMAS
from meridian.core.servers import (
    ServerBootstrapKeyRequest,
    ServerBootstrapKeyResult,
    ServerConnectionDraft,
    ServerProfile,
    ServerValidateRequest,
    ServerValidateResult,
)
from meridian.core.workflow import InputField, InputOption, InputSection, WorkflowCatalogEntry, WorkflowPlan


class EmptyData(CoreModel):
    """Empty data object used by failed or cancelled envelopes."""


class SchemaCatalogEntry(CoreModel):
    """Discoverable schema catalog entry."""

    name: str
    title: str
    description: str
    commands: list[str] = Field(default_factory=list)
    json_schema: dict[str, Any] | None = Field(default=None, alias="schema")


class ApiSchemasResult(CoreModel):
    """Result for `meridian api schemas --json`."""

    schemas: list[SchemaCatalogEntry]


class ApiCommandsResult(CoreModel):
    """Result for `meridian api commands --json`."""

    commands: list[CommandCatalogEntry]


class ApiSchemaResult(CoreModel):
    """Result for `meridian api schema NAME --envelope`."""

    name: str
    json_schema: dict[str, Any] = Field(alias="schema")


class ApiWorkflowResult(CoreModel):
    """Result for `meridian api workflow NAME --json`."""

    name: str
    workflow: WorkflowPlan


class EventTypeCatalog(CoreModel):
    """Public event types emitted by meridian-core operation streams."""

    schema_version: Literal["meridian.event-types/v1"] = Field(default="meridian.event-types/v1", alias="schema")
    event_schema: str = "event"
    types: list[str]


def event_type_catalog() -> dict[str, Any]:
    """Return the public event type catalog."""
    return EventTypeCatalog(types=list(EVENT_TYPES)).model_dump(mode="json", by_alias=True)


class _ContractEnvelope(CoreModel):
    """Strict wire envelope base used by command-specific contracts."""

    schema_version: OutputSchema = Field(alias="schema")
    meridian_version: str
    command: str
    operation_id: str
    started_at: str
    duration_ms: int
    status: OutputStatus
    exit_code: int
    summary: Summary
    warnings: list[MeridianError]
    errors: list[MeridianError]


# ---------------------------------------------------------------------------
# Envelope factory — replaces per-command copy-pasted envelope classes
# ---------------------------------------------------------------------------


def _make_command_envelope(
    prefix: str,
    command: str,
    data_type: type,
    *,
    success_statuses: Any = None,
    failure_data_type: Any = EmptyData,
    doc: str = "",
) -> tuple[type[_ContractEnvelope], type[_ContractEnvelope], type[RootModel]]:  # type: ignore[type-arg]
    """Build (SuccessEnvelope, TerminalEnvelope, OutputEnvelope) for a command.

    The generated models produce identical JSON Schemas to the hand-written
    classes they replace — same ``$defs`` keys, same discriminator mapping.

    Args:
        prefix: PascalCase prefix for class names (e.g. ``"FleetStatus"``).
        command: Literal command string (e.g. ``"fleet.status"``).
        data_type: Pydantic model for the success ``data`` field.
        success_statuses: Literal type for success status values.
            Defaults to ``Literal["ok"]``.
        failure_data_type: Pydantic model for the terminal ``data`` field.
            Defaults to :class:`EmptyData`.
        doc: Docstring for the root envelope model.
    """
    cmd_literal = Literal[command]  # type: ignore[valid-type]
    if success_statuses is None:
        success_statuses = Literal["ok"]

    success_cls = create_model(
        f"_{prefix}SuccessEnvelope",
        __base__=_ContractEnvelope,
        command=(cmd_literal, ...),
        status=(success_statuses, ...),
        data=(data_type, ...),
        errors=(list[MeridianError], Field(max_length=0)),
    )

    terminal_cls = create_model(
        f"_{prefix}TerminalEnvelope",
        __base__=_ContractEnvelope,
        command=(cmd_literal, ...),
        status=(Literal["failed", "cancelled"], ...),
        data=(failure_data_type, ...),
        errors=(list[MeridianError], Field(min_length=1)),
    )

    union_type = Annotated[Union[success_cls, terminal_cls], Field(discriminator="status")]  # type: ignore[valid-type]
    root_cls = type(f"{prefix}OutputEnvelope", (RootModel[union_type],), {"__doc__": doc})

    return success_cls, terminal_cls, root_cls


# --- Plan ---
_PlanSuccessEnvelope, _PlanTerminalEnvelope, PlanOutputEnvelope = _make_command_envelope(
    "Plan",
    "plan",
    PlanCommandData,
    success_statuses=Literal["changed", "no_changes"],
    doc="Envelope schema for `meridian plan --json`.",
)

# --- Apply ---
_ApplySuccessEnvelope, _ApplyTerminalEnvelope, ApplyOutputEnvelope = _make_command_envelope(
    "Apply",
    "apply",
    ApplyCommandData,
    success_statuses=Literal["changed", "no_changes"],
    failure_data_type=ApplyResult | CompiledApplyResult | CompiledApplyPreview | EmptyData,
    doc="Envelope schema for `meridian apply --json`.",
)


class ApplyFailureData(RootModel[ApplyResult | CompiledApplyResult | CompiledApplyPreview | EmptyData]):
    """Failure data schema for `meridian apply --json`."""


class DeployCommandData(RootModel[DeployResult | DeployPlan]):
    """Success data for `meridian deploy --json`.

    Normal execution returns DeployResult. Dry-run returns DeployPlan.
    """


# --- Deploy (three-way discriminated union — kept manual) ---


class _DeployChangedEnvelope(_ContractEnvelope):
    command: Literal["deploy"]
    status: Literal["changed"]
    data: DeployResult
    errors: list[MeridianError] = Field(max_length=0)


class _DeployPlanEnvelope(_ContractEnvelope):
    command: Literal["deploy"]
    status: Literal["ok"]
    data: DeployPlan
    errors: list[MeridianError] = Field(max_length=0)


class _DeployTerminalEnvelope(_ContractEnvelope):
    command: Literal["deploy"]
    status: Literal["failed", "cancelled"]
    data: EmptyData
    errors: list[MeridianError] = Field(min_length=1)


class DeployOutputEnvelope(
    RootModel[
        Annotated[_DeployChangedEnvelope | _DeployPlanEnvelope | _DeployTerminalEnvelope, Field(discriminator="status")]
    ]
):
    """Envelope schema for `meridian deploy --json`."""


# --- Standard ok/fail envelopes ---
_, _, FleetStatusOutputEnvelope = _make_command_envelope(
    "FleetStatus",
    "fleet.status",
    FleetStatus,
    doc="Envelope schema for `meridian fleet status --json`.",
)

_, _, FleetInventoryOutputEnvelope = _make_command_envelope(
    "FleetInventory",
    "fleet.inventory",
    FleetInventory,
    doc="Envelope schema for `meridian fleet inventory --json`.",
)

_, _, ClientListOutputEnvelope = _make_command_envelope(
    "ClientList",
    "client.list",
    ClientListResult,
    doc="Envelope schema for `meridian client list --json`.",
)

_, _, ClientShowOutputEnvelope = _make_command_envelope(
    "ClientShow",
    "client.show",
    ClientShowResult,
    doc="Envelope schema for `meridian client show --json`.",
)

_, _, ApiSchemasOutputEnvelope = _make_command_envelope(
    "ApiSchemas",
    "api.schemas",
    ApiSchemasResult,
    doc="Envelope schema for `meridian api schemas --json`.",
)

_, _, ApiCommandsOutputEnvelope = _make_command_envelope(
    "ApiCommands",
    "api.commands",
    ApiCommandsResult,
    doc="Envelope schema for `meridian api commands --json`.",
)

_, _, ApiSchemaOutputEnvelope = _make_command_envelope(
    "ApiSchema",
    "api.schema",
    ApiSchemaResult,
    doc="Envelope schema for `meridian api schema NAME --envelope`.",
)

_, _, ApiWorkflowOutputEnvelope = _make_command_envelope(
    "ApiWorkflow",
    "api.workflow",
    ApiWorkflowResult,
    doc="Envelope schema for `meridian api workflow NAME --json`.",
)


OutcomeCategory = ErrorCategory | Literal["none"]


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
        CommandOutcome(status="cancelled", exit_code=130, category="cancelled", meaning="cancelled by the user"),
    ]


def _plan_outcomes() -> list[CommandOutcome]:
    return [
        CommandOutcome(status="no_changes", exit_code=0, category="none", meaning="desired state already matches"),
        CommandOutcome(status="changed", exit_code=2, category="none", meaning="changes pending"),
        CommandOutcome(status="failed", exit_code=2, category="user", meaning="user or configuration error"),
        CommandOutcome(status="failed", exit_code=3, category="system", meaning="system or infrastructure failure"),
        CommandOutcome(status="failed", exit_code=1, category="bug", meaning="unexpected Meridian bug"),
        CommandOutcome(status="cancelled", exit_code=130, category="cancelled", meaning="cancelled by the user"),
    ]


def _apply_outcomes() -> list[CommandOutcome]:
    return [
        CommandOutcome(status="no_changes", exit_code=0, category="none", meaning="desired state already matches"),
        CommandOutcome(status="changed", exit_code=0, category="none", meaning="changes were applied"),
        CommandOutcome(status="failed", exit_code=2, category="user", meaning="user or configuration error"),
        CommandOutcome(status="failed", exit_code=3, category="system", meaning="system or infrastructure failure"),
        CommandOutcome(status="failed", exit_code=1, category="bug", meaning="unexpected Meridian bug"),
        CommandOutcome(status="cancelled", exit_code=130, category="cancelled", meaning="cancelled by the user"),
    ]


def _deploy_outcomes() -> list[CommandOutcome]:
    return [
        CommandOutcome(status="changed", exit_code=0, category="none", meaning="server was deployed"),
        CommandOutcome(status="ok", exit_code=0, category="none", meaning="deploy request was validated or planned"),
        CommandOutcome(status="failed", exit_code=2, category="user", meaning="user or configuration error"),
        CommandOutcome(status="failed", exit_code=3, category="system", meaning="system or infrastructure failure"),
        CommandOutcome(status="failed", exit_code=1, category="bug", meaning="unexpected Meridian bug"),
        CommandOutcome(status="cancelled", exit_code=130, category="cancelled", meaning="cancelled by the user"),
    ]


_SCHEMAS: dict[str, type[BaseModel]] = {
    "output-envelope": OutputEnvelope,
    "apply": ApplyResult,
    "apply-command-data": ApplyCommandData,
    "apply-action": ApplyActionResult,
    "apply-counts": ApplyCounts,
    "apply-envelope": ApplyOutputEnvelope,
    "apply-failure": ApplyFailureData,
    "compiled-apply-action": CompiledApplyActionResult,
    "compiled-apply-preview": CompiledApplyPreview,
    "compiled-apply-result": CompiledApplyResult,
    "api-commands": ApiCommandsResult,
    "api-commands-envelope": ApiCommandsOutputEnvelope,
    "api-schema": ApiSchemaResult,
    "api-schema-envelope": ApiSchemaOutputEnvelope,
    "api-schemas": ApiSchemasResult,
    "api-schemas-envelope": ApiSchemasOutputEnvelope,
    "api-workflow": ApiWorkflowResult,
    "api-workflow-envelope": ApiWorkflowOutputEnvelope,
    "client-list-envelope": ClientListOutputEnvelope,
    "client-show-envelope": ClientShowOutputEnvelope,
    "plan-envelope": PlanOutputEnvelope,
    "fleet-status-envelope": FleetStatusOutputEnvelope,
    "fleet-inventory-envelope": FleetInventoryOutputEnvelope,
    "event": Event,
    "error": MeridianError,
    "operation": OperationEnvelope,
    "deploy-operation-result": DeployOperationResult,
    "operation-cancel": OperationCancel,
    "operation-diagnostics": OperationDiagnostics,
    "operation-error": OperationError,
    "operation-events": OperationEvents,
    "operation-list": OperationList,
    "operation-result": OperationResultEnvelope,
    "operation-snapshot": OperationSnapshot,
    "operation-start": OperationStart,
    "summary": Summary,
    "client-list": ClientListResult,
    "client-show": ClientShowResult,
    "client-name-request": ClientNameRequest,
    "server-add-request": ServerAddRequest,
    "server-bootstrap-key-request": ServerBootstrapKeyRequest,
    "server-bootstrap-key-result": ServerBootstrapKeyResult,
    "server-connection-draft": ServerConnectionDraft,
    "server-profile": ServerProfile,
    "server-remove-request": ServerRemoveRequest,
    "server-validate-request": ServerValidateRequest,
    "server-validate-result": ServerValidateResult,
    **TOPOLOGY_SCHEMAS,
    "node-add-request": NodeAddRequest,
    "node-target-request": NodeTargetRequest,
    "relay-deploy-request": RelayDeployRequest,
    "relay-target-request": RelayTargetRequest,
    "deploy-command-data": DeployCommandData,
    "deploy-envelope": DeployOutputEnvelope,
    "deploy-request": DeployRequest,
    "deploy-result": DeployResult,
    "deploy-workflow-answers": DeployWorkflowAnswers,
    "deploy-plan": DeployPlan,
    "deploy-ports": DeployPorts,
    "deploy-cluster-state": DeployClusterState,
    "deploy-node-state": DeployNodeState,
    "remote-target": RemoteTarget,
    "command-spec": CommandSpec,
    "remote-command-result": RemoteCommandResult,
    "put-bytes-spec": PutBytesSpec,
    "put-text-spec": PutTextSpec,
    "workflow-plan": WorkflowPlan,
    "workflow-catalog-entry": WorkflowCatalogEntry,
    "input-field": InputField,
    "input-option": InputOption,
    "input-section": InputSection,
    "event-type-catalog": EventTypeCatalog,
    "schema-catalog-entry": SchemaCatalogEntry,
    "empty-data": EmptyData,
    "plan-result": PlanResult,
    "plan-command-data": PlanCommandData,
    "plan-action": PlanActionResult,
    "plan-counts": PlanCounts,
    "compiled-plan-drift": CompiledPlanDriftResult,
    "compiled-plan-resource": CompiledPlanResourceResult,
    "compiled-plan-result": CompiledPlanResult,
    "fleet-status": FleetStatus,
    "fleet-inventory": FleetInventory,
    "command-contract": CommandContract,
    "command-catalog-entry": CommandCatalogEntry,
    "command-outcome": CommandOutcome,
}

_COMMAND_CONTRACTS: dict[str, CommandContract] = {
    "apply": CommandContract(
        command="apply",
        argv=["apply"],
        envelope_schema="apply-envelope",
        data_schema="apply-command-data",
        failure_data_schema="apply-failure",
        error_schema="error",
        statuses=["no_changes", "changed", "failed", "cancelled"],
        outcomes=_apply_outcomes(),
        exit_codes={
            "0": "desired state was already converged or changes were applied",
            "1": "unexpected Meridian bug",
            "2": "user/config error",
            "3": "system or infrastructure failure",
            "130": "cancelled by the user",
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
        statuses=["changed", "ok", "failed", "cancelled"],
        outcomes=_deploy_outcomes(),
        exit_codes={
            "0": "server was deployed or deploy request was validated/planned",
            "1": "unexpected Meridian bug",
            "2": "user/config error",
            "3": "system or infrastructure failure",
            "130": "cancelled by the user",
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
        statuses=["ok", "failed", "cancelled"],
        outcomes=_standard_outcomes("ok", "command contracts were listed"),
        exit_codes={
            "0": "command contracts were listed",
            "1": "unexpected Meridian bug",
            "2": "user/config error",
            "3": "system or infrastructure failure",
            "130": "cancelled by the user",
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
        statuses=["ok", "failed", "cancelled"],
        outcomes=_standard_outcomes("ok", "schema was found"),
        exit_codes={
            "0": "schema was found",
            "1": "unexpected Meridian bug",
            "2": "schema name is unknown",
            "3": "system or infrastructure failure",
            "130": "cancelled by the user",
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
        statuses=["ok", "failed", "cancelled"],
        outcomes=_standard_outcomes("ok", "schema catalog was listed"),
        exit_codes={
            "0": "schema catalog was listed",
            "1": "unexpected Meridian bug",
            "2": "user/config error",
            "3": "system or infrastructure failure",
            "130": "cancelled by the user",
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
        statuses=["ok", "failed", "cancelled"],
        outcomes=_standard_outcomes("ok", "workflow contract was returned"),
        exit_codes={
            "0": "workflow contract was returned",
            "1": "unexpected Meridian bug",
            "2": "workflow name is unknown",
            "3": "system or infrastructure failure",
            "130": "cancelled by the user",
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
        statuses=["ok", "failed", "cancelled"],
        outcomes=_standard_outcomes("ok", "client list was collected"),
        exit_codes={
            "0": "client list was collected",
            "1": "unexpected Meridian bug",
            "2": "user/config error",
            "3": "system or infrastructure failure",
            "130": "cancelled by the user",
        },
        machine_flags=["--json"],
        stability="stable",
        description="List panel clients as redacted metadata plus aggregate status counts.",
    ),
    "client.show": CommandContract(
        command="client.show",
        argv=["client", "show", "NAME"],
        envelope_schema="client-show-envelope",
        data_schema="client-show",
        failure_data_schema="empty-data",
        error_schema="error",
        statuses=["ok", "failed", "cancelled"],
        outcomes=_standard_outcomes("ok", "client was found"),
        exit_codes={
            "0": "client was found",
            "1": "unexpected Meridian bug",
            "2": "client not found or input/config error",
            "3": "system or infrastructure failure",
            "130": "cancelled by the user",
        },
        machine_flags=["--json"],
        stability="stable",
        description="Return one panel client and redacted handoff links.",
    ),
    "plan": CommandContract(
        command="plan",
        argv=["plan"],
        envelope_schema="plan-envelope",
        data_schema="plan-command-data",
        failure_data_schema="empty-data",
        error_schema="error",
        statuses=["no_changes", "changed", "failed", "cancelled"],
        outcomes=_plan_outcomes(),
        exit_codes={
            "0": "desired state already matches actual state",
            "1": "unexpected Meridian bug",
            "2": "changes pending; user/config errors also use category=user in the error envelope",
            "3": "system or infrastructure failure",
            "130": "cancelled by the user",
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
        statuses=["ok", "failed", "cancelled"],
        outcomes=_standard_outcomes("ok", "fleet status was collected"),
        exit_codes={
            "0": "fleet status was collected; inspect data.summary.health and warnings for degraded state",
            "1": "unexpected Meridian bug",
            "2": "user/config error",
            "3": "system or infrastructure failure",
            "130": "cancelled by the user",
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
        statuses=["ok", "failed", "cancelled"],
        outcomes=_standard_outcomes("ok", "inventory was collected"),
        exit_codes={
            "0": "inventory was collected; plan --json is the drift/apply authority",
            "1": "unexpected Meridian bug",
            "2": "user/config error",
            "3": "system or infrastructure failure",
            "130": "cancelled by the user",
        },
        machine_flags=["--json"],
        stability="stable",
        description="Return the configured fleet topology plus live panel observations when available.",
    ),
}

_COMMAND_SCHEMAS: dict[str, str] = {
    command: contract.envelope_schema for command, contract in _COMMAND_CONTRACTS.items()
}


def schema_names() -> list[str]:
    """Return stable schema names."""
    return sorted(_SCHEMAS)


def schema_model(name: str) -> type[BaseModel]:
    """Return the Pydantic model for a stable schema name."""
    try:
        return _SCHEMAS[name]
    except KeyError as exc:
        available = ", ".join(schema_names())
        raise ValueError(f"Unknown schema {name!r}. Available: {available}") from exc


def schema_for(name: str) -> dict[str, Any]:
    """Return JSON Schema for one meridian-core contract."""
    return schema_model(name).model_json_schema(mode="serialization", by_alias=True)


def validate_command_envelope(payload: OutputEnvelope) -> OutputEnvelope:
    """Validate a produced envelope against its advertised command contract."""
    contract = _COMMAND_CONTRACTS.get(payload.command)
    if contract is None:
        return payload
    schema_model(contract.envelope_schema).model_validate(payload.model_dump(mode="json", by_alias=True))
    categories: set[OutcomeCategory] = {error.category for error in payload.errors} if payload.errors else {"none"}
    if not any(
        outcome.status == payload.status and outcome.exit_code == payload.exit_code and outcome.category in categories
        for outcome in contract.outcomes
    ):
        expected = ", ".join(
            f"{outcome.status}/{outcome.exit_code}/{outcome.category}" for outcome in contract.outcomes
        )
        raise ValueError(
            f"{payload.command} produced unsupported outcome "
            f"{payload.status}/{payload.exit_code}/{', '.join(sorted(categories))}; expected one of: {expected}"
        )
    return payload


def schema_catalog(*, include_schemas: bool = False) -> list[dict[str, Any]]:
    """Return schema metadata, optionally including full JSON Schemas."""
    catalog = []
    for name in schema_names():
        model = schema_model(name)
        commands = [command for command, schema_name in _COMMAND_SCHEMAS.items() if schema_name == name]
        entry = SchemaCatalogEntry(
            name=name,
            title=model.__name__,
            description=(model.__doc__ or "").strip(),
            commands=commands,
            schema=schema_for(name) if include_schemas else None,
        )
        catalog.append(entry.model_dump(mode="json", by_alias=True, exclude_none=True))
    return catalog


def command_contracts() -> list[CommandContract]:
    """Return stable command contracts for migrated process API commands."""
    return [_COMMAND_CONTRACTS[name] for name in sorted(_COMMAND_CONTRACTS)]


def command_catalog(*, include_schemas: bool = False) -> list[dict[str, Any]]:
    """Return command contract metadata, optionally including JSON Schemas."""
    catalog: list[dict[str, Any]] = []
    for contract in command_contracts():
        entry = CommandCatalogEntry(
            **contract.model_dump(mode="json"),
            envelope=schema_for(contract.envelope_schema) if include_schemas else None,
            data=schema_for(contract.data_schema) if include_schemas else None,
            failure_data=schema_for(contract.failure_data_schema) if include_schemas else None,
            error=schema_for(contract.error_schema) if include_schemas else None,
        )
        catalog.append(entry.model_dump(mode="json", by_alias=True, exclude_none=True))
    return catalog
