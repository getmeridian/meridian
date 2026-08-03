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
from meridian.core.clients import (
    ClientAddResult,
    ClientListResult,
    ClientRemoveResult,
    ClientShowResult,
    ClientStatusResult,
)
from meridian.core.command_catalog import (
    CommandCatalogEntry,
    CommandContract,
    CommandOutcome,
    OutcomeCategory,
    command_contract,
    command_contracts,
    command_schema_bindings,
)
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
from meridian.core.fleet import FleetInventory, FleetStatus, NodeListResult, RelayListResult
from meridian.core.models import (
    CoreModel,
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
from meridian.core.verification import (
    ProbeResult,
    TestResult,
    VerificationAggregate,
    VerificationCheck,
    VerificationContext,
    VerificationCounts,
    VerificationFinding,
    VerificationResult,
    VerificationTarget,
)
from meridian.core.workflow import InputField, InputOption, InputSection, WorkflowCatalogEntry, WorkflowPlan


class EmptyData(CoreModel):
    """Empty data object used by envelopes without command-specific failure data."""


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
        status=(Literal["failed"], ...),
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
    failure_data_type=PlanResult | CompiledPlanResult | EmptyData,
    doc="Envelope schema for `meridian plan --json`.",
)


class PlanFailureData(RootModel[PlanResult | CompiledPlanResult | EmptyData]):
    """Failure data schema for `meridian plan --json`."""


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


_, _, ProbeOutputEnvelope = _make_command_envelope(
    "Probe",
    "probe",
    ProbeResult,
    failure_data_type=ProbeResult | EmptyData,
    doc="Envelope schema for probe JSON output.",
)

_, _, TestOutputEnvelope = _make_command_envelope(
    "Test",
    "test",
    TestResult,
    failure_data_type=TestResult | EmptyData,
    doc="Envelope schema for test JSON output.",
)


class ProbeFailureData(RootModel[ProbeResult | EmptyData]):
    """Failure data schema for probe JSON output."""


class TestFailureData(RootModel[TestResult | EmptyData]):
    """Failure data schema for test JSON output."""


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
    status: Literal["failed"]
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

_, _, ClientAddOutputEnvelope = _make_command_envelope(
    "ClientAdd",
    "client.add",
    ClientAddResult,
    doc="Envelope schema for `meridian client add --json`.",
)

_, _, ClientRemoveOutputEnvelope = _make_command_envelope(
    "ClientRemove",
    "client.remove",
    ClientRemoveResult,
    doc="Envelope schema for `meridian client remove --json`.",
)

_, _, ClientEnableOutputEnvelope = _make_command_envelope(
    "ClientEnable",
    "client.enable",
    ClientStatusResult,
    doc="Envelope schema for `meridian client enable --json`.",
)

_, _, ClientDisableOutputEnvelope = _make_command_envelope(
    "ClientDisable",
    "client.disable",
    ClientStatusResult,
    doc="Envelope schema for `meridian client disable --json`.",
)

_, _, NodeListOutputEnvelope = _make_command_envelope(
    "NodeList",
    "node.list",
    NodeListResult,
    doc="Envelope schema for `meridian --json node list`.",
)

_, _, RelayListOutputEnvelope = _make_command_envelope(
    "RelayList",
    "relay.list",
    RelayListResult,
    doc="Envelope schema for `meridian --json relay list`.",
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
    "client-add-envelope": ClientAddOutputEnvelope,
    "client-remove-envelope": ClientRemoveOutputEnvelope,
    "client-enable-envelope": ClientEnableOutputEnvelope,
    "client-disable-envelope": ClientDisableOutputEnvelope,
    "node-list-envelope": NodeListOutputEnvelope,
    "relay-list-envelope": RelayListOutputEnvelope,
    "plan-envelope": PlanOutputEnvelope,
    "plan-failure": PlanFailureData,
    "probe-envelope": ProbeOutputEnvelope,
    "probe-failure": ProbeFailureData,
    "probe-result": ProbeResult,
    "test-envelope": TestOutputEnvelope,
    "test-failure": TestFailureData,
    "test-result": TestResult,
    "verification-aggregate": VerificationAggregate,
    "verification-check": VerificationCheck,
    "verification-context": VerificationContext,
    "verification-counts": VerificationCounts,
    "verification-finding": VerificationFinding,
    "verification-result": VerificationResult,
    "verification-target": VerificationTarget,
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
    "client-add": ClientAddResult,
    "client-remove": ClientRemoveResult,
    "client-status": ClientStatusResult,
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
    "node-list": NodeListResult,
    "relay-list": RelayListResult,
    "command-contract": CommandContract,
    "command-catalog-entry": CommandCatalogEntry,
    "command-outcome": CommandOutcome,
}


_COMMAND_SCHEMAS = command_schema_bindings()


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
    contract = command_contract(payload.command)
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
