"""Apply result contracts for meridian-core clients."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from meridian.core.models import CoreModel

CompiledApplyActionStatus = Literal["converged", "applied", "failed", "unknown", "skipped"]


class ApplyCounts(CoreModel):
    """Aggregate counts for apply execution."""

    actions: int
    succeeded: int
    failed: int
    skipped: int


class CompiledApplyActionResult(CoreModel):
    """Execution result for one V4 compiler resource."""

    resource_id: str
    status: CompiledApplyActionStatus
    success: bool
    changed: bool
    error: str = ""


class CompiledApplyResult(CoreModel):
    """Apply result for one generation-scoped V4 compiler plan."""

    plan_hash: str
    generation: int = Field(ge=0)
    all_succeeded: bool
    changed: bool
    summary: str
    exit_code: int
    counts: ApplyCounts
    actions: list[CompiledApplyActionResult] = Field(default_factory=list)

    def to_data(self) -> dict[str, Any]:
        from meridian.core.serde import to_plain

        return to_plain(self)


class ApplyCommandData(CompiledApplyResult):
    """Success data returned by apply execution."""


class CompiledApplyPreview(CoreModel):
    """Reviewed V4 compiler-plan metadata returned before confirmation."""

    plan_hash: str
    resource_count: int = Field(ge=0)

    def to_data(self) -> dict[str, Any]:
        from meridian.core.serde import to_plain

        return to_plain(self)


def build_compiled_apply_result(
    execution_result: Any,
    *,
    exit_code: int,
    summary: str,
) -> CompiledApplyResult:
    """Build a typed result from V4 compiler-resource executor output."""
    actions = [
        CompiledApplyActionResult(
            resource_id=item.action.resource.logical_id,
            status=item.status,
            success=item.succeeded,
            changed=item.changed,
            error=item.error,
        )
        for item in execution_result.results
    ]
    counts = ApplyCounts(
        actions=len(actions),
        succeeded=sum(1 for action in actions if action.success),
        failed=sum(1 for action in actions if not action.success and action.status != "skipped"),
        skipped=sum(1 for action in actions if action.status == "skipped"),
    )
    return CompiledApplyResult(
        plan_hash=execution_result.plan_hash,
        generation=execution_result.generation,
        all_succeeded=execution_result.all_succeeded,
        changed=execution_result.changed,
        summary=summary,
        exit_code=exit_code,
        counts=counts,
        actions=actions,
    )
