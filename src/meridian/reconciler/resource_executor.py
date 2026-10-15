"""Checkpointed executor for dependency-ordered V4 compiler resources."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from meridian.cluster import ActionCheckpoint, ClusterConfig, ManagedResourceBinding
from meridian.compiler.models import ConfigProfilePayload, ResourcePlan
from meridian.core.cancellation import (
    CancellationToken,
    OperationCancellation,
    OperationCancelledError,
)
from meridian.core.errors import MeridianError
from meridian.core.events import (
    PLAN_ACTION_COMPLETED,
    PLAN_ACTION_FAILED,
    PLAN_ACTION_SKIPPED,
    PLAN_ACTION_STARTED,
    CoreEventType,
)
from meridian.core.models import EventLevel, ResourceRef
from meridian.core.output import OperationContext
from meridian.core.reporters import Reporter, emit_event
from meridian.reconciler.fence import ApplyFence, local_fence_view
from meridian.reconciler.resources import (
    PendingPlanConflictError,
    ResourceAction,
    ResourceActionResult,
    ResourceApplyReceipt,
    ResourceDriver,
    ResourceDrivers,
    ResourceExecutionResult,
    ResourceInspection,
    ResourceInspectionResult,
    ResourceObservation,
    ResourceReconcileError,
    UnboundDependencyError,
    UnknownResourceOutcome,
    UnsupportedResourceRetirementError,
    build_resource_actions,
    observation_converges,
)
from meridian.reconciler.retirement import retire, retired_bindings, unretirable
from meridian.reconciler.stall_retry import StallNotice, StallRetry, stall_event_notice

PersistCluster = Callable[[ClusterConfig], None]
Clock = Callable[[], datetime]
AfterApply = Callable[[ResourceAction], None]
Observe = Callable[[ResourceAction, ManagedResourceBinding | None], ResourceObservation]


def environment_after_apply_barrier(action: ResourceAction) -> None:
    """Expose an opt-in process barrier for destructive crash-recovery labs."""
    target = os.environ.get("MERIDIAN_TEST_AFTER_APPLY_RESOURCE", "")
    if not target or action.resource.logical_id != target:
        return
    marker_value = os.environ.get("MERIDIAN_TEST_AFTER_APPLY_MARKER", "")
    if not marker_value:
        raise ResourceReconcileError("MERIDIAN_TEST_AFTER_APPLY_MARKER is required when the test barrier is enabled.")
    marker = Path(marker_value)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(action.idempotency_key + "\n", encoding="utf-8")
    while marker.exists():
        time.sleep(0.1)


def execute_resource_plan(
    plan: ResourcePlan,
    cluster: ClusterConfig,
    drivers: ResourceDrivers,
    *,
    persist: PersistCluster | None = None,
    clock: Clock | None = None,
    max_unknown_attempts: int = 2,
    after_apply: AfterApply | None = None,
    cancellation: CancellationToken | None = None,
    reporter: Reporter | None = None,
    operation: OperationContext | None = None,
    fence: ApplyFence | None = None,
) -> ResourceExecutionResult:
    """Apply a reviewed plan with durable checkpoints and mandatory observation.

    *fence* (the control server's record of the last committed generation) is
    taken before anything changes, so a stale or foreign copy of the state
    refuses instead of rewriting what another machine applied.

    *cancellation* is honored only between actions (and between provisioning
    steps inside an action). A started mutation always records its outcome
    first, so a cancelled apply leaves the generation pending with no
    ``running`` checkpoint, and re-running the same plan resumes it.

    *reporter* receives ``plan.action.*`` events (with *operation* sequencing
    them) so CLI, JSONL, and Engine clients can render progress.

    Observations that lose their SSH connection run again (``StallRetry``),
    reported as ``connection_stalled`` warnings; mutations never do (an
    uncertain one is observed first).
    """
    if max_unknown_attempts < 1:
        raise ValueError("max_unknown_attempts must be at least 1")
    validate_resource_plan_transition(plan, cluster)
    save = persist or (lambda state: state.save())
    now = clock or (lambda: datetime.now(UTC))
    stall = StallRetry(stop=cancellation.is_cancelled if cancellation is not None else None)
    if fence is not None:
        cluster.fleet_id = fence.acquire(local_fence_view(cluster))
    try:
        return _execute_fenced(
            plan,
            cluster,
            drivers,
            save=save,
            now=now,
            max_unknown_attempts=max_unknown_attempts,
            after_apply=after_apply,
            cancellation=cancellation,
            reporter=reporter,
            operation=operation,
            fence=fence,
            stall=stall,
        )
    finally:
        if fence is not None:
            fence.release()


def _execute_fenced(
    plan: ResourcePlan,
    cluster: ClusterConfig,
    drivers: ResourceDrivers,
    *,
    save: PersistCluster,
    now: Clock,
    max_unknown_attempts: int,
    after_apply: AfterApply | None,
    cancellation: CancellationToken | None,
    reporter: Reporter | None,
    operation: OperationContext | None,
    fence: ApplyFence | None,
    stall: StallRetry,
) -> ResourceExecutionResult:
    generation = _begin_apply(plan, cluster, save)
    actions = build_resource_actions(plan, generation)
    results: list[ResourceActionResult] = []
    succeeded: set[str] = set()

    report = _ActionReporter(reporter, operation, total=len(actions))
    for index, action in enumerate(actions, start=1):
        if fence is not None:
            fence.renew()
        if cancellation is not None and cancellation.is_cancelled():
            raise _apply_cancelled(results, len(actions), f"Stopped before {action.resource.logical_id}.")
        blocked = [dependency for dependency in action.resource.dependencies if dependency not in succeeded]
        if blocked:
            result = _record_blocked(action, blocked, cluster, save, now)
        else:
            report.started(action, index)
            driver = drivers.get(action.resource.payload.kind)
            if driver is None:
                result = _record_failure(
                    action,
                    cluster,
                    save,
                    now,
                    f"no resource driver registered for {action.resource.payload.kind}",
                )
            else:
                try:
                    result = _execute_action(
                        action,
                        driver,
                        cluster,
                        save,
                        now,
                        max_unknown_attempts=max_unknown_attempts,
                        after_apply=after_apply,
                        cancellation=cancellation,
                        observe=_retrying_observe(driver, stall, report.stall_notice(action)),
                    )
                except OperationCancelledError as exc:
                    raise _apply_cancelled(
                        results,
                        len(actions),
                        f"Stopped inside {action.resource.logical_id}: {exc}",
                        interrupted=True,
                    ) from exc
        report.finished(result, index)
        results.append(result)
        if result.succeeded:
            succeeded.add(action.resource.logical_id)

    execution = ResourceExecutionResult(
        plan_hash=plan.plan_hash,
        generation=generation,
        results=results,
    )
    if execution.all_succeeded and cancellation is not None and cancellation.is_cancelled():
        raise _apply_cancelled(results, len(actions), "Stopped before retiring removed resources.")
    if execution.all_succeeded:
        # Retire only after everything still desired has converged, so a
        # removal never strands traffic that the new plan has not yet carried.
        retired, errors = retire(retired_bindings(plan, cluster), drivers, cluster, save)
        execution = execution.model_copy(update={"retired": retired, "retirement_errors": errors})
    if execution.all_succeeded:
        _commit_generation(plan, generation, cluster, save)
        if fence is not None:
            fence.commit(generation, plan.plan_hash)
    return execution


class _ActionReporter:
    """Emit one started and one terminal event per executed action."""

    def __init__(self, reporter: Reporter | None, operation: OperationContext | None, *, total: int) -> None:
        self._reporter = reporter
        self._operation = operation or OperationContext()
        self._total = total

    def started(self, action: ResourceAction, index: int) -> None:
        self._emit(PLAN_ACTION_STARTED, action, index)

    def stall_notice(self, action: ResourceAction) -> StallNotice | None:
        if self._reporter is None:
            return None
        return stall_event_notice(self._reporter, self._operation, _resource_ref(action))

    def finished(self, result: ResourceActionResult, index: int) -> None:
        if result.succeeded:
            self._emit(PLAN_ACTION_COMPLETED, result.action, index, result=result)
        elif result.status == "skipped":
            self._emit(PLAN_ACTION_SKIPPED, result.action, index, result=result, level="warning")
        else:
            self._emit(PLAN_ACTION_FAILED, result.action, index, result=result, level="error")

    def _emit(
        self,
        event_type: CoreEventType,
        action: ResourceAction,
        index: int,
        *,
        result: ResourceActionResult | None = None,
        level: EventLevel = "info",
    ) -> None:
        if self._reporter is None:
            return
        data: dict[str, object] = {"index": index, "total": self._total}
        if result is not None:
            data.update({"status": result.status, "changed": result.changed})
        emit_event(
            self._reporter,
            self._operation,
            event_type,
            level=level,
            phase="apply",
            resource=_resource_ref(action),
            message=result.error if result is not None else "",
            data=data,
        )


def _resource_ref(action: ResourceAction) -> ResourceRef:
    payload = action.resource.payload
    return ResourceRef(kind=payload.kind, id=action.resource.logical_id, name=getattr(payload, "server_ref", ""))


def _retrying_observe(driver: ResourceDriver, stall: StallRetry, notice: StallNotice | None) -> Observe:
    """Observe through *stall*, so a dropped connection is redialed and read again."""

    def observe(action: ResourceAction, binding: ManagedResourceBinding | None) -> ResourceObservation:
        return stall.call(lambda: driver.observe(action, binding), notice)

    return observe


def validate_resource_plan_transition(
    plan: ResourcePlan,
    cluster: ClusterConfig,
) -> None:
    """Reject transitions that cannot run safely, before any state is written.

    Callers persist the new intent right after this check, so every refusal
    must happen here rather than once execution starts.
    """
    if cluster.pending_plan_hash and cluster.pending_plan_hash != plan.plan_hash:
        ambiguous = [
            checkpoint.resource_id
            for checkpoint in cluster.action_checkpoints.values()
            if checkpoint.generation == cluster.pending_generation and checkpoint.status in {"running", "unknown"}
        ]
        if ambiguous:
            resources = ", ".join(sorted(ambiguous))
            raise PendingPlanConflictError(
                f"Cannot replace pending plan {cluster.pending_plan_hash[:12]}; "
                f"remote outcome is unresolved for: {resources}."
            )

    blocked = unretirable(retired_bindings(plan, cluster))
    if blocked:
        raise UnsupportedResourceRetirementError(blocked)


def inspect_resource_plan(
    plan: ResourcePlan,
    cluster: ClusterConfig,
    drivers: ResourceDrivers,
) -> ResourceInspectionResult:
    """Observe every reviewed resource without writing state or mutating it."""
    stall = StallRetry()
    generation = _inspection_generation(plan, cluster)
    inspections: list[ResourceInspection] = []
    for action in build_resource_actions(plan, generation):
        driver = drivers.get(action.resource.payload.kind)
        if driver is None:
            inspections.append(
                ResourceInspection(
                    action=action,
                    error=(f"no resource driver registered for {action.resource.payload.kind}"),
                )
            )
            continue
        try:
            observation = _retrying_observe(driver, stall, None)(action, _binding(action, cluster))
        except UnboundDependencyError as exc:
            # Apply binds these dependencies first; until then this resource is a pending change.
            inspections.append(ResourceInspection(action=action, waiting_on=sorted(exc.logical_ids)))
            continue
        except Exception as exc:
            inspections.append(ResourceInspection(action=action, error=f"observation failed: {exc}"))
            continue
        inspections.append(ResourceInspection(action=action, observation=observation))
    return ResourceInspectionResult(
        plan_hash=plan.plan_hash,
        generation=generation,
        inspections=inspections,
        retiring=[binding.logical_id for binding in retired_bindings(plan, cluster)],
    )


def _inspection_generation(
    plan: ResourcePlan,
    cluster: ClusterConfig,
) -> int:
    if cluster.pending_plan_hash == plan.plan_hash and cluster.pending_generation > 0:
        return cluster.pending_generation
    if cluster.active_plan_hash == plan.plan_hash and cluster.active_generation > 0:
        return cluster.active_generation
    return max(cluster.active_generation, cluster.pending_generation) + 1


def _begin_apply(
    plan: ResourcePlan,
    cluster: ClusterConfig,
    persist: PersistCluster,
) -> int:
    if cluster.pending_plan_hash == plan.plan_hash and cluster.pending_generation > 0:
        generation = cluster.pending_generation
    elif cluster.active_plan_hash == plan.plan_hash and cluster.active_generation > 0:
        generation = cluster.active_generation
    else:
        generation = max(cluster.active_generation, cluster.pending_generation) + 1
        cluster.action_checkpoints = {}

    cluster.pending_generation = generation
    cluster.pending_plan_hash = plan.plan_hash
    persist(cluster)
    return generation


def _execute_action(
    action: ResourceAction,
    driver: ResourceDriver,
    cluster: ClusterConfig,
    persist: PersistCluster,
    clock: Clock,
    *,
    max_unknown_attempts: int,
    after_apply: AfterApply | None,
    cancellation: CancellationToken | None = None,
    observe: Observe | None = None,
) -> ResourceActionResult:
    observe = observe or driver.observe
    checkpoint = _checkpoint(action, cluster)
    binding = _binding(action, cluster)
    try:
        observation = observe(action, binding)
    except Exception as exc:
        return _record_failure(action, cluster, persist, clock, f"observation failed before apply: {exc}")
    if observation_converges(action, observation):
        _record_success(action, observation, binding, cluster, persist, clock)
        return ResourceActionResult(action=action, status="converged")

    # The retry budget is per run: checkpoint.attempts is a lifetime counter, and
    # an earlier failed run (e.g. ACME before DNS propagated) must not block the
    # operator's retry once the cause is fixed.
    run_attempts = 0
    while run_attempts < max_unknown_attempts:
        run_attempts += 1
        checkpoint.status = "running"
        checkpoint.attempts += 1
        checkpoint.last_error = ""
        checkpoint.updated_at = _timestamp(clock)
        persist(cluster)

        receipt = ResourceApplyReceipt()
        try:
            receipt = driver.apply(action, binding)
        except OperationCancelledError as exc:
            # Raised only at a provisioning step boundary: completed steps are
            # idempotent and the next run observes before re-applying.
            _record_cancelled(action, cluster, persist, clock, str(exc))
            raise
        except UnknownResourceOutcome as exc:
            checkpoint.status = "unknown"
            checkpoint.last_error = str(exc)
            checkpoint.updated_at = _timestamp(clock)
            persist(cluster)
            observed = _observe_unknown(action, observe, binding, cluster, persist, clock)
            if isinstance(observed, ResourceActionResult):
                return observed
            if observation_converges(action, observed):
                _record_success(action, observed, binding, cluster, persist, clock, receipt=receipt)
                return ResourceActionResult(action=action, status="applied", changed=True)
            if run_attempts < max_unknown_attempts and cancellation is not None and cancellation.is_cancelled():
                message = "unknown mutation was observed as not converged; cancelled before retrying"
                _record_cancelled(action, cluster, persist, clock, message)
                raise OperationCancelledError(
                    OperationCancellation(phase="apply", message=message, remote_changed=True)
                )
            if run_attempts < max_unknown_attempts:
                continue
            return _record_failure(
                action,
                cluster,
                persist,
                clock,
                "unknown mutation was observed as not converged; retry limit reached",
            )
        except Exception as exc:
            # Keep typed remediation: the failure summary is all the operator sees.
            hint = exc.hint if isinstance(exc, MeridianError) and exc.hint else ""
            detail = f"apply failed: {exc}" + (f" — {hint}" if hint else "")
            return _record_failure(action, cluster, persist, clock, detail)

        if after_apply is not None:
            after_apply(action)
        observed_binding = _binding_from_receipt(action, binding, receipt)
        try:
            observation = observe(action, observed_binding)
        except Exception as exc:
            checkpoint.status = "unknown"
            checkpoint.last_error = f"post-apply observation failed: {exc}"
            checkpoint.updated_at = _timestamp(clock)
            persist(cluster)
            return ResourceActionResult(
                action=action,
                status="unknown",
                changed=True,
                error=checkpoint.last_error,
            )
        if not observation_converges(action, observation):
            return _record_failure(
                action,
                cluster,
                persist,
                clock,
                "post-apply observation did not satisfy the reviewed hash and postconditions",
            )
        _record_success(action, observation, binding, cluster, persist, clock, receipt=receipt)
        return ResourceActionResult(action=action, status="applied", changed=True)

    return _record_failure(action, cluster, persist, clock, "action retry limit reached")


def _observe_unknown(
    action: ResourceAction,
    observe: Observe,
    binding: ManagedResourceBinding | None,
    cluster: ClusterConfig,
    persist: PersistCluster,
    clock: Clock,
) -> ResourceObservation | ResourceActionResult:
    try:
        return observe(action, binding)
    except Exception as exc:
        checkpoint = _checkpoint(action, cluster)
        checkpoint.status = "unknown"
        checkpoint.last_error = f"cannot resolve unknown outcome: {exc}"
        checkpoint.updated_at = _timestamp(clock)
        persist(cluster)
        return ResourceActionResult(
            action=action,
            status="unknown",
            changed=True,
            error=checkpoint.last_error,
        )


def _checkpoint(action: ResourceAction, cluster: ClusterConfig) -> ActionCheckpoint:
    existing = cluster.action_checkpoints.get(action.idempotency_key)
    if existing is not None:
        if (
            existing.resource_id != action.resource.logical_id
            or existing.expected_hash != action.expected_hash
            or existing.generation != action.generation
        ):
            raise ResourceReconcileError(
                f"Checkpoint {action.idempotency_key} does not match its immutable reviewed action."
            )
        return existing
    checkpoint = ActionCheckpoint(
        idempotency_key=action.idempotency_key,
        resource_id=action.resource.logical_id,
        expected_hash=action.expected_hash,
        generation=action.generation,
    )
    cluster.action_checkpoints[action.idempotency_key] = checkpoint
    return checkpoint


def _binding(action: ResourceAction, cluster: ClusterConfig) -> ManagedResourceBinding | None:
    return cluster.managed_bindings.get(f"{action.resource.logical_id}@{action.generation}")


def _binding_from_receipt(
    action: ResourceAction,
    binding: ManagedResourceBinding | None,
    receipt: ResourceApplyReceipt,
) -> ManagedResourceBinding | None:
    if binding is not None or not receipt.remote_id:
        return binding
    return ManagedResourceBinding(
        logical_id=action.resource.logical_id,
        resource_kind=action.resource.payload.kind,
        generation=action.generation,
        remote_id=receipt.remote_id,
        desired_hash=action.expected_hash,
    )


def _record_success(
    action: ResourceAction,
    observation: ResourceObservation,
    binding: ManagedResourceBinding | None,
    cluster: ClusterConfig,
    persist: PersistCluster,
    clock: Clock,
    *,
    receipt: ResourceApplyReceipt | None = None,
) -> None:
    remote_id = observation.remote_id or (receipt.remote_id if receipt is not None else "")
    key = f"{action.resource.logical_id}@{action.generation}"
    cluster.managed_bindings[key] = ManagedResourceBinding(
        logical_id=action.resource.logical_id,
        resource_kind=action.resource.payload.kind,
        generation=action.generation,
        remote_id=remote_id or (binding.remote_id if binding is not None else ""),
        desired_hash=action.expected_hash,
        observed_hash=observation.observed_hash,
        active=binding.active if binding is not None else False,
        adopted=binding.adopted if binding is not None else False,
        server_ref=str(getattr(action.resource.payload, "server_ref", "") or ""),
    )
    checkpoint = _checkpoint(action, cluster)
    checkpoint.status = "succeeded"
    checkpoint.observed_hash = observation.observed_hash
    checkpoint.remote_id = remote_id or (binding.remote_id if binding is not None else "")
    checkpoint.last_error = ""
    checkpoint.updated_at = _timestamp(clock)
    persist(cluster)


def _record_failure(
    action: ResourceAction,
    cluster: ClusterConfig,
    persist: PersistCluster,
    clock: Clock,
    error: str,
) -> ResourceActionResult:
    checkpoint = _checkpoint(action, cluster)
    checkpoint.status = "failed"
    checkpoint.last_error = error
    checkpoint.updated_at = _timestamp(clock)
    persist(cluster)
    return ResourceActionResult(action=action, status="failed", error=error)


def _record_cancelled(
    action: ResourceAction,
    cluster: ClusterConfig,
    persist: PersistCluster,
    clock: Clock,
    error: str,
) -> None:
    checkpoint = _checkpoint(action, cluster)
    checkpoint.status = "cancelled"
    checkpoint.last_error = error
    checkpoint.updated_at = _timestamp(clock)
    persist(cluster)


def _apply_cancelled(
    results: list[ResourceActionResult],
    total: int,
    detail: str,
    *,
    interrupted: bool = False,
) -> OperationCancelledError:
    """Describe where apply stopped; every checkpoint is already persisted."""
    return OperationCancelledError(
        OperationCancellation(
            phase="apply",
            message=f"Apply stopped after {len(results)} of {total} resource actions. {detail}",
            remote_changed=interrupted or any(result.changed for result in results),
        )
    )


def _record_blocked(
    action: ResourceAction,
    dependencies: list[str],
    cluster: ClusterConfig,
    persist: PersistCluster,
    clock: Clock,
) -> ResourceActionResult:
    error = f"blocked by unsuccessful dependencies: {', '.join(dependencies)}"
    checkpoint = _checkpoint(action, cluster)
    checkpoint.status = "skipped"
    checkpoint.last_error = error
    checkpoint.updated_at = _timestamp(clock)
    persist(cluster)
    return ResourceActionResult(action=action, status="skipped", error=error)


def _commit_generation(
    plan: ResourcePlan,
    generation: int,
    cluster: ClusterConfig,
    persist: PersistCluster,
) -> None:
    current_ids = {resource.logical_id for resource in plan.resources}
    for binding in cluster.managed_bindings.values():
        binding.active = binding.generation == generation and binding.logical_id in current_ids
    _drop_superseded_bindings(cluster, generation)
    current_workloads = {
        resource.payload.workload_id
        for resource in plan.resources
        if isinstance(resource.payload, ConfigProfilePayload)
    }
    for workload in cluster.workloads:
        workload.active = workload.generation == generation and workload.id in current_workloads
    cluster.active_generation = generation
    cluster.active_plan_hash = plan.plan_hash
    cluster.pending_generation = 0
    cluster.pending_plan_hash = ""
    persist(cluster)


def _drop_superseded_bindings(cluster: ClusterConfig, generation: int) -> None:
    """Forget older copies of a binding once a newer one holds the same remote identity.

    Every generation re-binds each resource, so without this cluster.yml grew with
    every apply. A (logical ID, remote ID) pair is what ownership checks match
    (``was_managed``), so keeping its newest binding keeps that evidence.
    """
    newest: dict[tuple[str, str], int] = {}
    for binding in cluster.managed_bindings.values():
        pair = (binding.logical_id, binding.remote_id)
        newest[pair] = max(newest.get(pair, 0), binding.generation)
    cluster.managed_bindings = {
        key: binding
        for key, binding in cluster.managed_bindings.items()
        if binding.active
        or binding.generation >= generation
        or newest[(binding.logical_id, binding.remote_id)] == binding.generation
    }


def _timestamp(clock: Clock) -> str:
    return clock().astimezone(UTC).isoformat().replace("+00:00", "Z")
