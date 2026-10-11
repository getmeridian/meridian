"""Plan result contracts for meridian-core clients."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from meridian.core.models import CoreModel
from meridian.core.resources import ResourceKind


class CompiledPlanDriftResult(CoreModel):
    """One V4 compiler resource that does not match remote state."""

    logical_id: str
    error: str = ""


class CompiledPlanResourceResult(CoreModel):
    """Secret-free identity for one reviewed V4 compiler resource."""

    logical_id: str
    kind: ResourceKind
    desired_hash: str
    dependencies: list[str] = Field(default_factory=list)


class CompiledPlanResult(CoreModel):
    """Read-only drift result for a V4 compiler plan."""

    plan_hash: str
    converged: bool
    summary: str
    exit_code: int
    state_changes: list[str] = Field(default_factory=list)
    observation_errors: list[CompiledPlanDriftResult] = Field(default_factory=list)
    drifted_resources: list[CompiledPlanDriftResult] = Field(default_factory=list)
    resources: list[CompiledPlanResourceResult] = Field(default_factory=list)

    def to_data(self) -> dict[str, Any]:
        from meridian.core.serde import to_plain

        return to_plain(self)


class PlanCommandData(CompiledPlanResult):
    """Success data returned by plan execution."""
