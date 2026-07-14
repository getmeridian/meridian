"""Checkpointed executor for dependency-ordered V4 compiler resources."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from meridian.cluster import ActionCheckpoint, ClusterConfig, ManagedResourceBinding
from meridian.compiler.models import ConfigProfilePayload, ResourcePlan
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
    UnknownResourceOutcome,
    build_resource_actions,
    observation_converges,
)

PersistCluster = Callable[[ClusterConfig], None]
Clock = Callable[[], datetime]


def execute_resource_plan(
    plan: ResourcePlan,
    cluster: ClusterConfig,
    drivers: ResourceDrivers,
    *,
    persist: PersistCluster | None = None,
    clock: Clock | None = None,
    max_unknown_attempts: int = 2,
) -> ResourceExecutionResult:
    """Apply a reviewed plan with durable checkpoints and mandatory observation."""
    if max_unknown_attempts < 1:
        raise ValueError("max_unknown_attempts must be at least 1")
    save = persist or (lambda state: state.save())
    now = clock or (lambda: datetime.now(UTC))
    generation = _begin_apply(plan, cluster, save)
    actions = build_resource_actions(plan, generation)
    results: list[ResourceActionResult] = []
    succeeded: set[str] = set()

    for action in actions:
        blocked = [dependency for dependency in action.resource.dependencies if dependency not in succeeded]
        if blocked:
            result = _record_blocked(action, blocked, cluster, save, now)
        else:
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
                result = _execute_action(
                    action,
                    driver,
                    cluster,
                    save,
                    now,
                    max_unknown_attempts=max_unknown_attempts,
                )
        results.append(result)
        if result.succeeded:
            succeeded.add(action.resource.logical_id)

    execution = ResourceExecutionResult(
        plan_hash=plan.plan_hash,
        generation=generation,
        results=results,
    )
    if execution.all_succeeded:
        _commit_generation(plan, generation, cluster, save)
    return execution


def inspect_resource_plan(
    plan: ResourcePlan,
    cluster: ClusterConfig,
    drivers: ResourceDrivers,
) -> ResourceInspectionResult:
    """Observe every reviewed resource without writing state or mutating it."""
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
            observation = driver.observe(
                action,
                _binding(action, cluster),
            )
        except Exception as exc:
            inspections.append(
                ResourceInspection(
                    action=action,
                    error=f"observation failed: {exc}",
                )
            )
            continue
        inspections.append(
            ResourceInspection(
                action=action,
                observation=observation,
            )
        )
    return ResourceInspectionResult(
        plan_hash=plan.plan_hash,
        generation=generation,
        inspections=inspections,
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
) -> ResourceActionResult:
    checkpoint = _checkpoint(action, cluster)
    binding = _binding(action, cluster)
    try:
        observation = driver.observe(action, binding)
    except Exception as exc:
        return _record_failure(action, cluster, persist, clock, f"observation failed before apply: {exc}")
    if observation_converges(action, observation):
        _record_success(action, observation, binding, cluster, persist, clock)
        return ResourceActionResult(action=action, status="converged")

    while checkpoint.attempts < max_unknown_attempts:
        checkpoint.status = "running"
        checkpoint.attempts += 1
        checkpoint.last_error = ""
        checkpoint.updated_at = _timestamp(clock)
        persist(cluster)

        receipt = ResourceApplyReceipt()
        try:
            receipt = driver.apply(action, binding)
        except UnknownResourceOutcome as exc:
            checkpoint.status = "unknown"
            checkpoint.last_error = str(exc)
            checkpoint.updated_at = _timestamp(clock)
            persist(cluster)
            observed = _observe_unknown(action, driver, binding, cluster, persist, clock)
            if isinstance(observed, ResourceActionResult):
                return observed
            if observation_converges(action, observed):
                _record_success(action, observed, binding, cluster, persist, clock, receipt=receipt)
                return ResourceActionResult(action=action, status="applied", changed=True)
            if checkpoint.attempts < max_unknown_attempts:
                continue
            return _record_failure(
                action,
                cluster,
                persist,
                clock,
                "unknown mutation was observed as not converged; retry limit reached",
            )
        except Exception as exc:
            return _record_failure(action, cluster, persist, clock, f"apply failed: {exc}")

        observed_binding = _binding_from_receipt(action, binding, receipt)
        try:
            observation = driver.observe(action, observed_binding)
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
    driver: ResourceDriver,
    binding: ManagedResourceBinding | None,
    cluster: ClusterConfig,
    persist: PersistCluster,
    clock: Clock,
) -> ResourceObservation | ResourceActionResult:
    try:
        return driver.observe(action, binding)
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


def _timestamp(clock: Clock) -> str:
    return clock().astimezone(UTC).isoformat().replace("+00:00", "Z")
