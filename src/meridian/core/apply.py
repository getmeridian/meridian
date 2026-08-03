"""Apply result contracts for meridian-core clients."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, RootModel

from meridian.core.models import CoreModel
from meridian.core.plan import PlanActionResult, PlanResult, build_plan_action_result, build_plan_result

ApplyActionStatus = Literal["succeeded", "failed", "skipped"]
CompiledApplyActionStatus = Literal["converged", "applied", "failed", "unknown", "skipped"]


class ApplyActionResult(CoreModel):
    """Execution result for one plan action."""

    action: PlanActionResult
    status: ApplyActionStatus
    success: bool
    error: str = ""


class ApplyCounts(CoreModel):
    """Aggregate counts for apply execution."""

    actions: int
    succeeded: int
    failed: int
    skipped: int


class ApplyResult(CoreModel):
    """Final apply result for process/API clients."""

    all_succeeded: bool
    summary: str
    exit_code: int
    plan: PlanResult
    counts: ApplyCounts
    actions: list[ApplyActionResult] = Field(default_factory=list)

    def to_data(self) -> dict[str, Any]:
        from meridian.core.serde import to_plain

        return to_plain(self)


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


class ApplyCommandData(RootModel[ApplyResult | CompiledApplyResult]):
    """Success data returned by legacy and V4 apply execution."""


class CompiledApplyPreview(CoreModel):
    """Reviewed V4 compiler-plan metadata returned before confirmation."""

    plan_hash: str
    resource_count: int = Field(ge=0)

    def to_data(self) -> dict[str, Any]:
        from meridian.core.serde import to_plain

        return to_plain(self)


def _apply_status(result: Any) -> ApplyActionStatus:
    if result.success:
        return "succeeded"
    if str(result.error).lower().startswith("skipped"):
        return "skipped"
    return "failed"


def build_apply_result(
    plan: Any,
    execution_result: Any,
    *,
    exit_code: int,
    summary: str | None = None,
    all_succeeded: bool | None = None,
) -> ApplyResult:
    """Build a typed, JSON-ready apply result from executor output."""
    raw_actions = list(plan.actions)
    plan_index_by_id = {id(action): index for index, action in enumerate(raw_actions)}
    plan_result = build_plan_result(plan, exit_code=0 if plan.is_empty else 2)
    execution_order_by_plan_index = {action.plan_index: action.execution_order for action in plan_result.actions}
    actions = []
    for fallback_index, result in enumerate(execution_result.results):
        plan_index = plan_index_by_id.get(id(result.action), fallback_index)
        status = _apply_status(result)
        actions.append(
            ApplyActionResult(
                action=build_plan_action_result(
                    result.action,
                    plan_index=plan_index,
                    execution_order=execution_order_by_plan_index.get(plan_index, fallback_index + 1),
                ),
                status=status,
                success=result.success,
                error=result.error,
            )
        )
    counts = ApplyCounts(
        actions=len(actions),
        succeeded=sum(1 for action in actions if action.status == "succeeded"),
        failed=sum(1 for action in actions if action.status == "failed"),
        skipped=sum(1 for action in actions if action.status == "skipped"),
    )
    return ApplyResult(
        all_succeeded=execution_result.all_succeeded if all_succeeded is None else all_succeeded,
        summary=execution_result.summary() if summary is None else summary,
        exit_code=exit_code,
        plan=plan_result,
        counts=counts,
        actions=actions,
    )


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
