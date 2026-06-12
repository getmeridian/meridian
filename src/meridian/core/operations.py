"""Operation contracts shared by Engine and Studio clients."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from meridian.core.deploy import DeployResult
from meridian.core.models import CoreModel, Event

OperationState = Literal[
    "queued", "running", "succeeded", "failed", "cancel_requested", "cancelled", "completed_after_cancel"
]
OperationKind = Literal["deploy"]


class OperationSnapshot(CoreModel):
    """Public state summary for a long-running local Engine operation."""

    id: str
    kind: OperationKind
    state: OperationState
    request_target: str
    created_at: str
    updated_at: str
    event_count: int
    warning_count: int = 0
    error_count: int = 0
    last_seq: int = 0
    current_phase: str = ""
    latest_event: Event | None = None
    cancelable: bool = False
    has_result: bool = False
    has_error: bool = False


class OperationError(CoreModel):
    """Terminal operation error safe for Studio and diagnostics."""

    message: str
    hint: str = ""
    category: Literal["user", "system", "bug", "cancelled"] = "system"
    retryable: bool = False
    last_seq: int = 0
    code: str = ""
    exit_code: int = 1
    details: dict[str, Any] = Field(default_factory=dict)


class OperationList(CoreModel):
    """List of recent or active local Engine operations."""

    schema_version: Literal["meridian.operations/v1"] = Field(default="meridian.operations/v1", alias="schema")
    operations: list[OperationSnapshot]


class OperationEnvelope(CoreModel):
    """Single operation snapshot response."""

    schema_version: Literal["meridian.operation/v1"] = Field(default="meridian.operation/v1", alias="schema")
    operation: OperationSnapshot


class OperationStart(CoreModel):
    """Operation start response."""

    schema_version: Literal["meridian.operation-start/v1"] = Field(
        default="meridian.operation-start/v1", alias="schema"
    )
    operation: OperationSnapshot


class OperationCancel(CoreModel):
    """Operation cancellation response."""

    schema_version: Literal["meridian.operation-cancel/v1"] = Field(
        default="meridian.operation-cancel/v1",
        alias="schema",
    )
    operation: OperationSnapshot


class OperationEvents(CoreModel):
    """Incremental event replay response."""

    schema_version: Literal["meridian.operation-events/v1"] = Field(
        default="meridian.operation-events/v1",
        alias="schema",
    )
    operation_id: str
    after_seq: int = 0
    latest_seq: int = 0
    events: list[Event]


class OperationResultEnvelope(CoreModel):
    """Terminal or in-progress operation result response."""

    schema_version: Literal["meridian.operation-result/v1"] = Field(
        default="meridian.operation-result/v1",
        alias="schema",
    )
    operation: OperationSnapshot
    result: dict[str, Any] | None = None
    error: OperationError | None = None


class DeployOperationResult(CoreModel):
    """Successful deploy operation payload returned by local Engine."""

    schema_version: Literal["meridian.deploy-operation-result/v1"] = Field(
        default="meridian.deploy-operation-result/v1",
        alias="schema",
    )
    result: DeployResult
    envelope: dict[str, Any] | None = None


class OperationDiagnostics(CoreModel):
    """Redacted diagnostic bundle for an operation."""

    schema_version: Literal["meridian.operation-diagnostics/v1"] = Field(
        default="meridian.operation-diagnostics/v1",
        alias="schema",
    )
    operation: OperationSnapshot
    events: list[Event]
    result: dict[str, Any] | None = None
    error: OperationError | None = None
    request: dict[str, Any] = Field(default_factory=dict)
    markdown: str = ""
