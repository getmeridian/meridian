"""Reconciler — compare desired state with actual state and produce a plan.

The reconciler is the engine behind ``meridian plan`` and ``meridian apply``.
It compares the desired topology declared in ``cluster.yml`` (v2) with the
actual state fetched from the Remnawave panel API and SSH, then produces a
typed list of actions to converge them.

Key principle: ``compute_plan()`` is a pure function — no I/O, no side
effects, fully unit-testable. The executor runs the plan by calling into
existing provisioning code.
"""

from meridian.reconciler.diff import Plan, PlanAction, PlanActionKind, compute_plan
from meridian.reconciler.executor import ActionHandler
from meridian.reconciler.state import ActualState, DesiredState

__all__ = [
    "ActionHandler",
    "ActualState",
    "DesiredState",
    "Plan",
    "PlanAction",
    "PlanActionKind",
    "compute_plan",
]
