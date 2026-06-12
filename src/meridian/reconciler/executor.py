"""Plan executor — runs reconciliation actions using existing provisioning.

Does NOT reimplement SSH provisioning or API calls. Instead, it calls
into the existing functions from ``commands/setup.py`` and ``provision/``.
Each action is executed in dependency order (nodes → relays → clients).

Node provisioning is parallelized when multiple nodes are independent.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from meridian.reconciler.diff import Plan, PlanAction, PlanActionKind

if TYPE_CHECKING:
    from meridian.cluster import ClusterConfig
    from meridian.remnawave import MeridianPanel

#: Callback signature for plan action handlers.
#: Each handler receives the action to execute, a panel client for API
#: calls, and the cluster config for state management.
ActionHandler = Callable[["PlanAction", "MeridianPanel", "ClusterConfig"], None]

logger = logging.getLogger("meridian.reconciler")


@dataclass
class ActionResult:
    """Result of executing a single plan action."""

    action: PlanAction
    success: bool = True
    error: str = ""


@dataclass
class ExecutionResult:
    """Result of executing an entire plan."""

    results: list[ActionResult] = field(default_factory=list)

    @property
    def all_succeeded(self) -> bool:
        return all(r.success for r in self.results)

    @property
    def failed(self) -> list[ActionResult]:
        return [r for r in self.results if not r.success]

    def summary(self) -> str:
        succeeded = sum(1 for r in self.results if r.success)
        failed_count = len(self.failed)
        if failed_count == 0:
            return f"All {succeeded} action(s) completed successfully."
        return f"{succeeded} succeeded, {failed_count} failed."


def _run_action(
    action: PlanAction,
    handler: ActionHandler,
    panel: MeridianPanel,
    cluster: ClusterConfig,
) -> ActionResult:
    """Execute a single action and return the result."""
    try:
        handler(action, panel, cluster)
        logger.info("Action succeeded: %s %s", action.kind.value, action.target)
        return ActionResult(action=action, success=True)
    except Exception as e:
        logger.error("Action failed: %s %s — %s", action.kind.value, action.target, e)
        return ActionResult(action=action, success=False, error=str(e))


def execute_plan(
    plan: Plan,
    panel: MeridianPanel,
    cluster: ClusterConfig,
    *,
    callbacks: dict[PlanActionKind, ActionHandler] | None = None,
    max_parallel: int = 4,
) -> ExecutionResult:
    """Execute a reconciliation plan.

    Actions are run in dependency order: nodes first, then relays, then
    clients, then subscription page. Node ADD actions within the same
    phase are parallelized (each node is independent).

    Args:
        plan: The reconciliation plan to execute.
        panel: MeridianPanel instance for API calls.
        cluster: ClusterConfig instance for state management.
        callbacks: Optional dict mapping action kinds to handler functions.
            Each handler receives (action, panel, cluster) and should
            return True on success or raise on failure.
        max_parallel: Maximum number of concurrent node provisioning threads.
    """
    callbacks = callbacks or {}
    results: list[ActionResult] = []

    # Execute in dependency order.
    #
    # REMOVE_RELAY runs BEFORE ADD_RELAY so that when a relay is
    # replaced by another with the same human-readable name (different
    # IP), the new ADD does not match the old relay's hosts by remark
    # and inherit doomed UUIDs that the later REMOVE then deletes.
    # See Bug #3 in the v4-declarative plan. REMOVE_NODE stays last —
    # nodes may still be referenced by relay/host metadata until those
    # are cleaned up.
    order = [
        PlanActionKind.REMOVE_CLIENT,
        PlanActionKind.REMOVE_RELAY,
        PlanActionKind.REMOVE_SUBSCRIPTION_PAGE,
        PlanActionKind.ADD_NODE,
        PlanActionKind.UPDATE_NODE,
        PlanActionKind.ADD_RELAY,
        PlanActionKind.UPDATE_RELAY,
        PlanActionKind.ADD_SUBSCRIPTION_PAGE,
        PlanActionKind.ADD_CLIENT,
        PlanActionKind.REMOVE_NODE,
    ]

    # Kinds that can be parallelized (independent per-server operations).
    # Each parallel worker gets its own MeridianPanel instance so the
    # underlying httpx.AsyncClient and the per-thread event loop (see
    # threading.local in remnawave.py::_run) are isolated. ClusterConfig
    # mutations are serialized by its RLock (saves are atomic).
    #
    # Only ADD_NODE is listed today. UPDATE_NODE could in principle run in
    # parallel across different hosts, but its handler mutates NodeEntry
    # attributes in-place BEFORE `_setup_redeploy` runs (so the redeploy
    # reads the new values) — a concurrent `cluster.save()` from another
    # worker would see the mutated-but-not-yet-reconciled state. Until
    # that's refactored to save-on-success-only, UPDATE_NODE stays serial.
    parallel_kinds = {PlanActionKind.ADD_NODE}

    # Destructive kinds — skip if a prior phase had failures (dependency safety)
    destructive_kinds = {
        PlanActionKind.UPDATE_RELAY,  # implemented as delete + recreate
        PlanActionKind.REMOVE_CLIENT,
        PlanActionKind.REMOVE_RELAY,
        PlanActionKind.REMOVE_SUBSCRIPTION_PAGE,
        PlanActionKind.REMOVE_NODE,
    }

    # Group actions by kind, preserving order within each kind
    by_kind: dict[PlanActionKind, list[PlanAction]] = {}
    for action in plan.actions:
        by_kind.setdefault(action.kind, []).append(action)

    has_failures = False
    for kind in order:
        actions = by_kind.get(kind, [])
        if not actions:
            continue

        # Skip destructive phases if prior phases failed
        if has_failures and kind in destructive_kinds:
            for action in actions:
                results.append(
                    ActionResult(
                        action=action,
                        success=False,
                        error="skipped: prior phase had failures",
                    )
                )
            continue

        handler = callbacks.get(kind)
        if handler is None:
            for action in actions:
                logger.warning("No handler for action kind: %s", kind)
                results.append(ActionResult(action=action, success=False, error="no handler registered"))
            has_failures = True
            continue

        # Parallelize independent actions (e.g., node provisioning).
        # Each worker gets its own MeridianPanel so httpx.AsyncClient and
        # the per-thread event loop are isolated. ClusterConfig is shared
        # but its RLock serializes the save path.
        if kind in parallel_kinds and len(actions) > 1 and max_parallel > 1:
            from meridian.remnawave import MeridianPanel

            def _make_worker_panel(p: MeridianPanel) -> MeridianPanel:
                """Clone a MeridianPanel for a worker thread."""
                if isinstance(p, MeridianPanel):
                    return MeridianPanel(p._base, p._token, timeout=p._timeout, max_retries=p._max_retries)
                return p  # tests may pass a mock — pass through

            with ThreadPoolExecutor(max_workers=min(max_parallel, len(actions))) as pool:
                futures = {}
                for action in actions:
                    worker_panel = _make_worker_panel(panel)
                    futures[pool.submit(_run_action, action, handler, worker_panel, cluster)] = (
                        action,
                        worker_panel,
                    )
                for future in as_completed(futures):
                    r = future.result()
                    results.append(r)
                    if not r.success:
                        has_failures = True
                    # Close per-worker panel to release httpx client
                    _, wp = futures[future]
                    if isinstance(wp, MeridianPanel):
                        wp.close()
        else:
            for action in actions:
                r = _run_action(action, handler, panel, cluster)
                results.append(r)
                if not r.success:
                    has_failures = True

    return ExecutionResult(results=results)
