"""Plan result contracts for meridian-core clients."""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import Field, RootModel

from meridian.core.models import CoreModel
from meridian.core.resources import ResourceKind

PlanOperation = Literal["add", "update", "remove", "replace"]
PlanResourceType = Literal["node", "client", "relay", "subscription_page"]
PlanPhase = Literal["provision", "configure", "deprovision", "replace"]
PlanSymbol = Literal["+", "~", "-"]
PlanActionKindValue = Literal[
    "add_node",
    "update_node",
    "remove_node",
    "add_client",
    "remove_client",
    "add_relay",
    "update_relay",
    "remove_relay",
    "add_subscription_page",
    "remove_subscription_page",
]


class PlanChange(CoreModel):
    field: str
    before: str
    after: str


class PlanActionResult(CoreModel):
    plan_index: int
    execution_order: int
    kind: PlanActionKindValue
    operation: PlanOperation
    resource_type: PlanResourceType
    resource_id: str
    target: str
    detail: str
    destructive: bool
    replacement: bool
    replacement_strategy: Literal["none", "delete_then_create"]
    from_extras: bool
    phase: PlanPhase
    requires_confirmation: bool
    destructive_reason: str
    change_set: list[PlanChange] = Field(default_factory=list)
    symbol: PlanSymbol
    can_run_parallel: bool = False


class PlanCounts(CoreModel):
    actions: int
    adds: int
    updates: int
    replacements: int
    removes: int
    destructive: int
    from_extras: int


class PlanResult(CoreModel):
    converged: bool
    summary: str
    exit_code: int
    counts: PlanCounts
    actions: list[PlanActionResult] = Field(default_factory=list)

    def to_data(self) -> dict[str, Any]:
        from meridian.core.serde import to_plain

        return to_plain(self)


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


class PlanCommandData(RootModel[PlanResult | CompiledPlanResult]):
    """Success data returned by legacy and V4 plan execution."""


def _operation(kind: str) -> PlanOperation:
    if kind.startswith("add_"):
        return "add"
    if kind.startswith("remove_"):
        return "remove"
    if kind == "update_relay":
        return "replace"
    return "update"


def _resource_type(kind: str) -> PlanResourceType:
    if kind.endswith("_node"):
        return "node"
    if kind.endswith("_client"):
        return "client"
    if kind.endswith("_relay"):
        return "relay"
    return "subscription_page"


def _phase(operation: PlanOperation) -> PlanPhase:
    if operation == "add":
        return "provision"
    if operation == "remove":
        return "deprovision"
    if operation == "replace":
        return "replace"
    return "configure"


def _symbol(operation: PlanOperation) -> PlanSymbol:
    if operation == "add":
        return "+"
    if operation == "remove":
        return "-"
    return "~"


def _replacement_strategy(operation: PlanOperation) -> Literal["none", "delete_then_create"]:
    if operation == "replace":
        return "delete_then_create"
    return "none"


PLAN_EXECUTION_KIND_ORDER: tuple[PlanActionKindValue, ...] = (
    "remove_client",
    "remove_relay",
    "remove_subscription_page",
    "add_node",
    "update_node",
    "add_relay",
    "update_relay",
    "add_subscription_page",
    "add_client",
    "remove_node",
)
PLAN_PARALLEL_KIND_VALUES: set[PlanActionKindValue] = {"add_node"}


def _kind_value(action: Any) -> PlanActionKindValue:
    return cast(PlanActionKindValue, action.kind.value)


def _execution_rank(kind: PlanActionKindValue) -> int:
    try:
        return PLAN_EXECUTION_KIND_ORDER.index(kind)
    except ValueError:
        return len(PLAN_EXECUTION_KIND_ORDER)


def _execution_order_lookup(actions: list[Any]) -> dict[int, int]:
    ordered = sorted(
        enumerate(actions),
        key=lambda indexed: (_execution_rank(_kind_value(indexed[1])), indexed[0]),
    )
    return {plan_index: execution_order for execution_order, (plan_index, _) in enumerate(ordered, start=1)}


def build_plan_action_result(action: Any, *, plan_index: int, execution_order: int) -> PlanActionResult:
    """Build one typed action result from a reconciler plan action."""
    kind = _kind_value(action)
    operation = _operation(kind)
    return PlanActionResult(
        plan_index=plan_index,
        execution_order=execution_order,
        kind=kind,
        operation=operation,
        resource_type=_resource_type(kind),
        resource_id=action.target,
        target=action.target,
        detail=action.detail,
        destructive=action.destructive,
        replacement=operation == "replace",
        replacement_strategy=_replacement_strategy(operation),
        from_extras=action.from_extras,
        phase=_phase(operation),
        requires_confirmation=action.destructive,
        destructive_reason=action.detail if action.destructive else "",
        change_set=[
            PlanChange(field=change.field, before=change.before, after=change.after)
            for change in getattr(action, "changes", [])
        ],
        symbol=_symbol(operation),
        can_run_parallel=kind in PLAN_PARALLEL_KIND_VALUES,
    )


def build_plan_result(plan: Any, *, exit_code: int) -> PlanResult:
    """Build a typed, JSON-ready plan result from the reconciler plan."""
    actions = []
    raw_actions = list(plan.actions)
    execution_order = _execution_order_lookup(raw_actions)
    for plan_index, action in enumerate(raw_actions):
        actions.append(
            build_plan_action_result(
                action,
                plan_index=plan_index,
                execution_order=execution_order[plan_index],
            )
        )
    counts = PlanCounts(
        actions=len(actions),
        adds=sum(1 for action in actions if action.operation == "add"),
        updates=sum(1 for action in actions if action.operation in ("update", "replace")),
        replacements=sum(1 for action in actions if action.operation == "replace"),
        removes=sum(1 for action in actions if action.operation == "remove"),
        destructive=sum(1 for action in actions if action.destructive),
        from_extras=sum(1 for action in actions if action.from_extras),
    )
    return PlanResult(
        converged=plan.is_empty,
        summary=plan.summary(),
        exit_code=exit_code,
        counts=counts,
        actions=actions,
    )
