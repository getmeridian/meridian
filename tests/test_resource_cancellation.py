"""Cooperative cancellation of checkpointed apply, and resume after cancel."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler.models import (
    COMPILER_VERSION,
    FirewallRulePayload,
    ProbePayload,
    ResourcePlan,
    compute_plan_hash,
    make_resource,
)
from meridian.core.cancellation import (
    OperationCancellation,
    OperationCancelledError,
)
from meridian.provision.steps import Provisioner, StepResult
from meridian.reconciler.resource_executor import execute_resource_plan, validate_resource_plan_transition
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceApplyReceipt,
    ResourceObservation,
    UnknownResourceOutcome,
    postcondition_key,
)
from tests.support.cancellation import CancellationFlag

_NOW = datetime(2026, 7, 14, 15, 0, tzinfo=UTC)


def _clock() -> datetime:
    return _NOW


def _plan(*, public_port: int = 443) -> ResourcePlan:
    firewall = make_resource(
        "firewall:srv-exit:tcp:443",
        FirewallRulePayload(server_ref="srv-exit", transport="tcp", port=public_port),
    )
    probe = make_resource(
        "probe:firewall",
        ProbePayload(probe="listener", target_ref=firewall.logical_id, expected="reachable"),
        dependencies=[firewall.logical_id],
    )
    resources = [firewall, probe]
    intent_hash = "1" * 64
    return ResourcePlan(
        intent_hash=intent_hash,
        plan_hash=compute_plan_hash(compiler_version=COMPILER_VERSION, intent_hash=intent_hash, resources=resources),
        resources=resources,
    )


class RemoteState:
    """Fake remote: an applied action stays converged across apply runs."""

    def __init__(self) -> None:
        self.converged: set[str] = set()
        self.applies: list[str] = []


class StatefulDriver:
    def __init__(self, remote: RemoteState, *, on_apply: Callable[[ResourceAction], Any] | None = None) -> None:
        self.remote = remote
        self.on_apply = on_apply

    def observe(self, action: ResourceAction, binding: ManagedResourceBinding | None) -> ResourceObservation:
        if action.expected_hash not in self.remote.converged:
            return ResourceObservation(exists=False)
        return ResourceObservation(
            exists=True,
            observed_hash=action.expected_hash,
            remote_id=action.resource.logical_id,
            satisfied_postconditions=sorted(
                postcondition_key(item.kind, item.target_ref, item.detail) for item in action.resource.postconditions
            ),
        )

    def apply(self, action: ResourceAction, binding: ManagedResourceBinding | None) -> ResourceApplyReceipt:
        self.remote.applies.append(action.resource.logical_id)
        if self.on_apply is not None:
            self.on_apply(action)
        self.remote.converged.add(action.expected_hash)
        return ResourceApplyReceipt(remote_id=action.resource.logical_id)


def _drivers(remote: RemoteState, **firewall_kwargs: Any) -> dict[str, Any]:
    return {"firewall_rule": StatefulDriver(remote, **firewall_kwargs), "probe": StatefulDriver(remote)}


def _statuses(cluster: ClusterConfig) -> dict[str, str]:
    return {checkpoint.resource_id: checkpoint.status for checkpoint in cluster.action_checkpoints.values()}


def _resume(plan: ResourcePlan, cluster: ClusterConfig, remote: RemoteState) -> None:
    result = execute_resource_plan(plan, cluster, _drivers(remote), persist=lambda _state: None, clock=_clock)
    assert result.all_succeeded
    assert cluster.active_plan_hash == plan.plan_hash
    assert cluster.pending_plan_hash == ""


def test_cancel_before_first_action_mutates_nothing_and_resumes() -> None:
    plan, cluster, remote = _plan(), ClusterConfig(), RemoteState()
    flag = CancellationFlag()
    flag.cancel()

    with pytest.raises(OperationCancelledError) as exc_info:
        execute_resource_plan(
            plan, cluster, _drivers(remote), persist=lambda _state: None, clock=_clock, cancellation=flag
        )

    assert exc_info.value.cancellation.phase == "apply"
    assert exc_info.value.cancellation.remote_changed is False
    assert remote.applies == []
    assert cluster.action_checkpoints == {}
    assert cluster.pending_plan_hash == plan.plan_hash and cluster.active_plan_hash == ""

    _resume(plan, cluster, remote)
    assert remote.applies == ["firewall:srv-exit:tcp:443", "probe:firewall"]


def test_cancel_between_actions_keeps_finished_work_and_resume_skips_it() -> None:
    plan, cluster, remote = _plan(), ClusterConfig(), RemoteState()
    flag = CancellationFlag()
    saved: list[dict[str, str]] = []

    with pytest.raises(OperationCancelledError) as exc_info:
        execute_resource_plan(
            plan,
            cluster,
            # The request arrives while the firewall mutation is in flight; it must finish first.
            _drivers(remote, on_apply=lambda _action: flag.cancel()),
            persist=lambda state: saved.append(_statuses(state)),
            clock=_clock,
            cancellation=flag,
        )

    assert exc_info.value.cancellation.remote_changed is True
    assert "1 of 2" in str(exc_info.value)
    assert _statuses(cluster) == {"firewall:srv-exit:tcp:443": "succeeded"}
    assert saved[-1] == {"firewall:srv-exit:tcp:443": "succeeded"}
    assert remote.applies == ["firewall:srv-exit:tcp:443"]

    _resume(plan, cluster, remote)
    # The finished firewall action is observed as converged, never re-applied.
    assert remote.applies == ["firewall:srv-exit:tcp:443", "probe:firewall"]


def test_cancel_at_a_provisioning_step_boundary_records_cancelled_not_running() -> None:
    plan, cluster, remote = _plan(), ClusterConfig(), RemoteState()
    flag = CancellationFlag()

    def stop_between_steps(_action: ResourceAction) -> None:
        flag.cancel()
        raise OperationCancelledError(
            OperationCancellation(phase="provision", message="Stopped before provisioning step 'nginx' (3 of 9).")
        )

    with pytest.raises(OperationCancelledError) as exc_info:
        execute_resource_plan(
            plan,
            cluster,
            _drivers(remote, on_apply=stop_between_steps),
            persist=lambda _state: None,
            clock=_clock,
            cancellation=flag,
        )

    assert exc_info.value.cancellation.phase == "apply"
    assert exc_info.value.cancellation.remote_changed is True
    assert "nginx" in str(exc_info.value)
    assert _statuses(cluster) == {"firewall:srv-exit:tcp:443": "cancelled"}
    # A clean step boundary is not an unresolved outcome, so a revised plan may replace it.
    validate_resource_plan_transition(_plan(public_port=8443), cluster)

    _resume(plan, cluster, remote)
    assert _statuses(cluster) == {"firewall:srv-exit:tcp:443": "succeeded", "probe:firewall": "succeeded"}


def test_cancel_after_unknown_outcome_observed_unconverged_skips_the_retry() -> None:
    plan, cluster, remote = _plan(), ClusterConfig(), RemoteState()
    flag = CancellationFlag()

    class TimesOut(StatefulDriver):
        def apply(self, action: ResourceAction, binding: ManagedResourceBinding | None) -> ResourceApplyReceipt:
            self.remote.applies.append(action.resource.logical_id)
            flag.cancel()
            raise UnknownResourceOutcome("timed out")

    with pytest.raises(OperationCancelledError):
        execute_resource_plan(
            plan,
            cluster,
            {"firewall_rule": TimesOut(remote), "probe": StatefulDriver(remote)},
            persist=lambda _state: None,
            clock=_clock,
            cancellation=flag,
        )

    assert remote.applies == ["firewall:srv-exit:tcp:443"]
    assert _statuses(cluster) == {"firewall:srv-exit:tcp:443": "cancelled"}
    _resume(plan, cluster, remote)


class _Step:
    def __init__(self, name: str, on_run: Callable[[], None] | None = None) -> None:
        self.name = name
        self.on_run = on_run
        self.ran = False

    def run(self, conn: Any, ctx: Any) -> StepResult:
        self.ran = True
        if self.on_run is not None:
            self.on_run()
        return StepResult(name=self.name, status="changed")


def test_provisioner_finishes_the_running_step_then_stops_before_the_next() -> None:
    flag = CancellationFlag()
    steps = [_Step("packages", on_run=flag.cancel), _Step("docker"), _Step("nginx")]

    with pytest.raises(OperationCancelledError) as exc_info:
        Provisioner(steps).run(None, object(), cancellation=flag)

    assert [step.ran for step in steps] == [True, False, False]
    assert exc_info.value.cancellation.phase == "provision"
    assert exc_info.value.cancellation.remote_changed is True
    assert "'docker' (2 of 3)" in str(exc_info.value)


def test_provisioner_cancelled_before_first_step_reports_no_remote_change() -> None:
    flag = CancellationFlag()
    flag.cancel()
    step = _Step("packages")

    with pytest.raises(OperationCancelledError) as exc_info:
        Provisioner([step]).run(None, object(), cancellation=flag)

    assert step.ran is False
    assert exc_info.value.cancellation.remote_changed is False
