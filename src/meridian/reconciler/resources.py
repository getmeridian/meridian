"""Typed actions and observations for V4 compiled-resource reconciliation."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import Field, model_validator

from meridian.cluster import ManagedResourceBinding
from meridian.compiler.models import CompiledResource, ResourceKind, ResourcePlan, canonical_hash
from meridian.core.errors import MeridianError
from meridian.core.models import CoreModel

ResourceActionStatus = Literal["converged", "applied", "failed", "unknown", "skipped"]


class ResourceReconcileError(MeridianError):
    """A reviewed resource plan cannot be reconciled safely."""


class PendingPlanConflictError(ResourceReconcileError):
    """A different plan cannot replace a remotely ambiguous pending plan."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            hint="Resume the pending plan until every unknown action is observed, then review the replacement plan.",
            category="user",
        )


class UnknownResourceOutcome(ResourceReconcileError):
    """A resource mutation may have completed, so observation is required."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=True)


class ResourceAction(CoreModel):
    """One immutable, idempotent application of a reviewed compiler resource."""

    idempotency_key: str
    generation: int = Field(ge=0)
    plan_hash: str
    expected_hash: str
    resource: CompiledResource

    @model_validator(mode="after")
    def validate_reviewed_resource(self) -> ResourceAction:
        if self.expected_hash != self.resource.desired_hash:
            raise ValueError("Resource action expected hash does not match its reviewed payload.")
        expected_key = resource_idempotency_key(
            plan_hash=self.plan_hash,
            generation=self.generation,
            resource=self.resource,
        )
        if self.idempotency_key != expected_key:
            raise ValueError("Resource action idempotency key does not match its reviewed payload.")
        return self


class ResourceObservation(CoreModel):
    """Managed projection observed after reading one remote resource."""

    exists: bool
    observed_hash: str = ""
    remote_id: str = ""
    satisfied_postconditions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_postconditions(self) -> ResourceObservation:
        if self.satisfied_postconditions != sorted(set(self.satisfied_postconditions)):
            raise ValueError("Satisfied postconditions must be sorted and unique.")
        return self


class ResourceApplyReceipt(CoreModel):
    """Non-authoritative identity returned by a mutation before observation."""

    remote_id: str = ""


class ResourceActionResult(CoreModel):
    """Terminal result for one resource action."""

    action: ResourceAction
    status: ResourceActionStatus
    changed: bool = False
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status in {"converged", "applied"}


class ResourceExecutionResult(CoreModel):
    """Result of applying one dependency-ordered reviewed resource plan."""

    plan_hash: str
    generation: int
    results: list[ResourceActionResult] = Field(default_factory=list)

    @property
    def all_succeeded(self) -> bool:
        return all(result.succeeded for result in self.results)

    @property
    def changed(self) -> bool:
        return any(result.changed for result in self.results)

    @property
    def failed(self) -> list[ResourceActionResult]:
        return [result for result in self.results if not result.succeeded]


class ResourceInspection(CoreModel):
    """Read-only comparison of one reviewed resource with remote state."""

    action: ResourceAction
    observation: ResourceObservation | None = None
    error: str = ""

    @property
    def converged(self) -> bool:
        return not self.error and self.observation is not None and observation_converges(self.action, self.observation)


class ResourceInspectionResult(CoreModel):
    """Read-only drift result for a complete reviewed resource plan."""

    plan_hash: str
    generation: int
    inspections: list[ResourceInspection] = Field(default_factory=list)

    @property
    def converged(self) -> bool:
        return all(inspection.converged for inspection in self.inspections)

    @property
    def drifted(self) -> list[ResourceInspection]:
        return [inspection for inspection in self.inspections if not inspection.converged]


class ResourceDriver(Protocol):
    """Runtime adapter for one finite compiler resource kind."""

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation: ...

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt: ...


ResourceDrivers = dict[ResourceKind, ResourceDriver]


def resource_idempotency_key(
    *,
    plan_hash: str,
    generation: int,
    resource: CompiledResource,
) -> str:
    """Derive the stable mutation key from the complete reviewed identity."""
    return canonical_hash(
        {
            "schema": "meridian.resource-action/v1",
            "plan_hash": plan_hash,
            "generation": generation,
            "logical_id": resource.logical_id,
            "expected_hash": resource.desired_hash,
        }
    )


def build_resource_actions(plan: ResourcePlan, generation: int) -> list[ResourceAction]:
    """Lift a compiler plan into immutable generation-scoped actions."""
    return [
        ResourceAction(
            idempotency_key=resource_idempotency_key(
                plan_hash=plan.plan_hash,
                generation=generation,
                resource=resource,
            ),
            generation=generation,
            plan_hash=plan.plan_hash,
            expected_hash=resource.desired_hash,
            resource=resource,
        )
        for resource in plan.resources
    ]


def postcondition_key(kind: str, target_ref: str, detail: str = "") -> str:
    """Return a stable key used by observers to attest postconditions."""
    return canonical_hash({"kind": kind, "target_ref": target_ref, "detail": detail})


def observation_converges(action: ResourceAction, observation: ResourceObservation) -> bool:
    """Check identity, managed hash, and every reviewed postcondition."""
    required = {
        postcondition_key(condition.kind, condition.target_ref, condition.detail)
        for condition in action.resource.postconditions
    }
    return (
        observation.exists
        and observation.observed_hash == action.expected_hash
        and required.issubset(observation.satisfied_postconditions)
    )
