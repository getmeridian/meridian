"""Checkpoint and recovery tests for compiled-resource reconciliation."""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest

from meridian.cluster import ActionCheckpoint, ClusterConfig, ManagedResourceBinding
from meridian.compiler.models import (
    COMPILER_VERSION,
    FirewallRulePayload,
    ProbePayload,
    ResourcePlan,
    compute_plan_hash,
    make_resource,
)
from meridian.reconciler.resource_executor import (
    environment_after_apply_barrier,
    execute_resource_plan,
    inspect_resource_plan,
)
from meridian.reconciler.resources import (
    PendingPlanConflictError,
    ResourceAction,
    ResourceApplyReceipt,
    ResourceObservation,
    UnknownResourceOutcome,
    UnsupportedResourceRetirementError,
    build_resource_actions,
    postcondition_key,
)

_HASH_A = "a" * 64
_HASH_B = "b" * 64
_NOW = datetime(2026, 7, 14, 15, 0, tzinfo=UTC)


class ScriptedDriver:
    def __init__(
        self,
        observations: list[ResourceObservation | Exception],
        applies: list[ResourceApplyReceipt | Exception] | None = None,
        *,
        events: list[str] | None = None,
    ) -> None:
        self.observations = list(observations)
        self.applies = list(applies or [])
        self.events = events if events is not None else []
        self.observe_calls = 0
        self.apply_calls: list[str] = []

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        self.observe_calls += 1
        self.events.append(f"observe:{action.resource.logical_id}")
        outcome = self.observations.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        self.events.append(f"apply:{action.resource.logical_id}")
        self.apply_calls.append(action.idempotency_key)
        outcome = self.applies.pop(0) if self.applies else ResourceApplyReceipt()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _plan(*, with_probe: bool = False, public_port: int = 443) -> ResourcePlan:
    firewall = make_resource(
        "firewall:srv-exit:tcp:443",
        FirewallRulePayload(
            server_ref="srv-exit",
            transport="tcp",
            port=public_port,
        ),
    )
    resources = [firewall]
    if with_probe:
        resources.append(
            make_resource(
                "probe:firewall",
                ProbePayload(
                    probe="listener",
                    target_ref=firewall.logical_id,
                    expected="reachable",
                ),
                dependencies=[firewall.logical_id],
            )
        )
    intent_hash = "1" * 64
    return ResourcePlan(
        intent_hash=intent_hash,
        plan_hash=compute_plan_hash(
            compiler_version=COMPILER_VERSION,
            intent_hash=intent_hash,
            resources=resources,
        ),
        resources=resources,
    )


def _missing() -> ResourceObservation:
    return ResourceObservation(exists=False)


def _converged(action: ResourceAction, *, remote_id: str = "remote-1") -> ResourceObservation:
    postconditions = sorted(
        postcondition_key(item.kind, item.target_ref, item.detail) for item in action.resource.postconditions
    )
    return ResourceObservation(
        exists=True,
        observed_hash=action.expected_hash,
        remote_id=remote_id,
        satisfied_postconditions=postconditions,
    )


def _clock() -> datetime:
    return _NOW


class TestResourceActions:
    def test_actions_bind_generation_plan_hash_and_complete_reviewed_payload(self) -> None:
        plan = _plan()

        first = build_resource_actions(plan, generation=3)
        second = build_resource_actions(plan, generation=3)

        assert first == second
        assert first[0].expected_hash == first[0].resource.desired_hash
        assert first[0].resource.payload == plan.resources[0].payload
        with pytest.raises(Exception):
            first[0].expected_hash = _HASH_A  # type: ignore[misc]

    def test_generation_changes_idempotency_key(self) -> None:
        plan = _plan()
        one = build_resource_actions(plan, generation=1)[0]
        two = build_resource_actions(plan, generation=2)[0]
        assert one.idempotency_key != two.idempotency_key

    def test_environment_barrier_publishes_action_before_waiting(
        self,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        action = build_resource_actions(_plan(), generation=1)[0]
        marker = tmp_path / "after-apply.ready"
        observed: list[str] = []
        monkeypatch.setenv(
            "MERIDIAN_TEST_AFTER_APPLY_RESOURCE",
            action.resource.logical_id,
        )
        monkeypatch.setenv("MERIDIAN_TEST_AFTER_APPLY_MARKER", str(marker))

        def release(_seconds: float) -> None:
            observed.append(marker.read_text(encoding="utf-8").strip())
            marker.unlink()

        monkeypatch.setattr(
            "meridian.reconciler.resource_executor.time.sleep",
            release,
        )

        environment_after_apply_barrier(action)

        assert observed == [action.idempotency_key]


class TestResourceInspection:
    def test_observes_active_generation_without_mutating_or_applying(self) -> None:
        plan = _plan()
        action = build_resource_actions(plan, generation=4)[0]
        cluster = ClusterConfig(
            active_generation=4,
            active_plan_hash=plan.plan_hash,
        )
        before = copy.deepcopy({key: value for key, value in vars(cluster).items() if key != "_lock"})
        driver = ScriptedDriver([_converged(action)])

        result = inspect_resource_plan(
            plan,
            cluster,
            {"firewall_rule": driver},
        )

        assert result.converged
        assert result.generation == 4
        assert result.drifted == []
        assert driver.apply_calls == []
        assert {key: value for key, value in vars(cluster).items() if key != "_lock"} == before

    def test_reports_missing_and_failed_observations_as_drift(self) -> None:
        plan = _plan(with_probe=True)
        firewall = ScriptedDriver([_missing()])
        probe = ScriptedDriver([OSError("probe unavailable")])

        result = inspect_resource_plan(
            plan,
            ClusterConfig(),
            {
                "firewall_rule": firewall,
                "probe": probe,
            },
        )

        assert not result.converged
        assert len(result.drifted) == 2
        assert result.drifted[0].observation == _missing()
        assert result.drifted[1].error == ("observation failed: probe unavailable")
        assert firewall.apply_calls == []
        assert probe.apply_calls == []


class TestCheckpointedExecution:
    def test_rejects_intent_shrink_before_state_or_remote_mutation(self) -> None:
        plan = _plan()
        retired = ManagedResourceBinding(
            logical_id="host:retired",
            resource_kind="host",
            generation=1,
            remote_id="host-1",
            desired_hash=_HASH_A,
            observed_hash=_HASH_A,
            active=True,
        )
        cluster = ClusterConfig(
            managed_bindings={"host:retired@1": retired},
            active_generation=1,
            active_plan_hash=_HASH_B,
        )
        driver = ScriptedDriver([])
        saves: list[ClusterConfig] = []

        with pytest.raises(
            UnsupportedResourceRetirementError,
            match="host:retired",
        ):
            execute_resource_plan(
                plan,
                cluster,
                {"firewall_rule": driver},
                persist=saves.append,
                clock=_clock,
            )

        assert driver.observe_calls == 0
        assert driver.apply_calls == []
        assert saves == []
        assert cluster.pending_plan_hash == ""

    def test_applies_in_dependency_order_and_commits_generation(self) -> None:
        plan = _plan(with_probe=True)
        actions = build_resource_actions(plan, generation=1)
        events: list[str] = []
        firewall = ScriptedDriver(
            [_missing(), _converged(actions[0], remote_id="firewall-1")],
            [ResourceApplyReceipt(remote_id="firewall-1")],
            events=events,
        )
        probe = ScriptedDriver(
            [_missing(), _converged(actions[1], remote_id="probe-1")],
            events=events,
        )
        cluster = ClusterConfig()
        saves: list[tuple[int, str, list[str]]] = []

        result = execute_resource_plan(
            plan,
            cluster,
            {"firewall_rule": firewall, "probe": probe},
            persist=lambda state: saves.append(
                (
                    state.pending_generation,
                    state.pending_plan_hash,
                    [checkpoint.status for checkpoint in state.action_checkpoints.values()],
                )
            ),
            clock=_clock,
        )

        assert result.all_succeeded
        assert result.changed
        assert events.index("apply:firewall:srv-exit:tcp:443") < events.index("observe:probe:firewall")
        assert cluster.active_generation == 1
        assert cluster.active_plan_hash == plan.plan_hash
        assert cluster.pending_generation == 0
        assert cluster.pending_plan_hash == ""
        assert all(binding.active for binding in cluster.managed_bindings.values())
        assert all(checkpoint.status == "succeeded" for checkpoint in cluster.action_checkpoints.values())
        assert saves

    def test_after_apply_barrier_runs_between_mutation_and_observation(self) -> None:
        plan = _plan()
        action = build_resource_actions(plan, generation=1)[0]
        events: list[str] = []
        driver = ScriptedDriver(
            [_missing(), _converged(action)],
            [ResourceApplyReceipt(remote_id="firewall-1")],
            events=events,
        )

        result = execute_resource_plan(
            plan,
            ClusterConfig(),
            {"firewall_rule": driver},
            persist=lambda _state: None,
            clock=_clock,
            after_apply=lambda current: events.append(f"barrier:{current.resource.logical_id}"),
        )

        assert result.all_succeeded
        assert events == [
            "observe:firewall:srv-exit:tcp:443",
            "apply:firewall:srv-exit:tcp:443",
            "barrier:firewall:srv-exit:tcp:443",
            "observe:firewall:srv-exit:tcp:443",
        ]

    def test_matching_observation_is_a_noop_but_is_still_reobserved(self) -> None:
        plan = _plan()
        action = build_resource_actions(plan, generation=1)[0]
        cluster = ClusterConfig(active_generation=1, active_plan_hash=plan.plan_hash)
        driver = ScriptedDriver([_converged(action)])

        result = execute_resource_plan(
            plan,
            cluster,
            {"firewall_rule": driver},
            persist=lambda _state: None,
            clock=_clock,
        )

        assert result.all_succeeded
        assert not result.changed
        assert result.results[0].status == "converged"
        assert driver.observe_calls == 1
        assert driver.apply_calls == []
        assert cluster.active_generation == 1

    def test_failed_dependency_blocks_dependent_resource(self) -> None:
        plan = _plan(with_probe=True)
        firewall = ScriptedDriver([_missing()], [RuntimeError("firewall denied")])
        probe = ScriptedDriver([])
        cluster = ClusterConfig()

        result = execute_resource_plan(
            plan,
            cluster,
            {"firewall_rule": firewall, "probe": probe},
            persist=lambda _state: None,
            clock=_clock,
        )

        assert [item.status for item in result.results] == ["failed", "skipped"]
        assert probe.observe_calls == 0
        assert cluster.active_generation == 0
        assert cluster.pending_generation == 1

    def test_old_generation_remains_active_until_resumed_cutover_succeeds(self) -> None:
        old_plan = _plan(public_port=8443)
        new_plan = _plan()
        old_binding = ManagedResourceBinding(
            logical_id="firewall:srv-exit:tcp:443",
            resource_kind="firewall_rule",
            generation=1,
            remote_id="old-firewall",
            desired_hash=old_plan.resources[0].desired_hash,
            observed_hash=old_plan.resources[0].desired_hash,
            active=True,
        )
        cluster = ClusterConfig(
            managed_bindings={"firewall:srv-exit:tcp:443@1": old_binding},
            active_generation=1,
            active_plan_hash=old_plan.plan_hash,
        )
        failed = ScriptedDriver([_missing()], [RuntimeError("temporary failure")])

        first = execute_resource_plan(
            new_plan,
            cluster,
            {"firewall_rule": failed},
            persist=lambda _state: None,
            clock=_clock,
        )

        assert not first.all_succeeded
        assert cluster.active_generation == 1
        assert cluster.pending_generation == 2
        assert old_binding.active

        action = build_resource_actions(new_plan, generation=2)[0]
        recovered = ScriptedDriver(
            [_missing(), _converged(action, remote_id="new-firewall")],
            [ResourceApplyReceipt(remote_id="new-firewall")],
        )
        second = execute_resource_plan(
            new_plan,
            cluster,
            {"firewall_rule": recovered},
            persist=lambda _state: None,
            clock=_clock,
        )

        assert second.all_succeeded
        assert cluster.active_generation == 2
        assert not old_binding.active
        assert cluster.managed_bindings["firewall:srv-exit:tcp:443@2"].active
        assert "firewall:srv-exit:tcp:443@1" in cluster.managed_bindings


class TestUnknownOutcomeRecovery:
    def test_unknown_outcome_is_observed_before_being_accepted(self) -> None:
        plan = _plan()
        action = build_resource_actions(plan, generation=1)[0]
        driver = ScriptedDriver(
            [_missing(), _converged(action)],
            [UnknownResourceOutcome("connection dropped after send")],
        )
        cluster = ClusterConfig()

        result = execute_resource_plan(
            plan,
            cluster,
            {"firewall_rule": driver},
            persist=lambda _state: None,
            clock=_clock,
        )

        assert result.all_succeeded
        assert driver.observe_calls == 2
        assert len(driver.apply_calls) == 1
        assert result.results[0].status == "applied"

    def test_unknown_outcome_retries_only_after_nonconverged_observation(self) -> None:
        plan = _plan()
        action = build_resource_actions(plan, generation=1)[0]
        driver = ScriptedDriver(
            [_missing(), _missing(), _converged(action)],
            [
                UnknownResourceOutcome("timeout"),
                ResourceApplyReceipt(remote_id="firewall-1"),
            ],
        )

        result = execute_resource_plan(
            plan,
            ClusterConfig(),
            {"firewall_rule": driver},
            persist=lambda _state: None,
            clock=_clock,
        )

        assert result.all_succeeded
        assert driver.observe_calls == 3
        assert len(driver.apply_calls) == 2
        assert len(set(driver.apply_calls)) == 1

    def test_failed_reobservation_leaves_unknown_checkpoint_and_never_retries(self) -> None:
        plan = _plan()
        driver = ScriptedDriver(
            [_missing(), OSError("panel unavailable")],
            [UnknownResourceOutcome("timeout")],
        )
        cluster = ClusterConfig()

        result = execute_resource_plan(
            plan,
            cluster,
            {"firewall_rule": driver},
            persist=lambda _state: None,
            clock=_clock,
        )

        assert result.results[0].status == "unknown"
        assert len(driver.apply_calls) == 1
        checkpoint = next(iter(cluster.action_checkpoints.values()))
        assert checkpoint.status == "unknown"
        assert "cannot resolve" in checkpoint.last_error

    def test_post_apply_observation_failure_is_unknown_not_failed(self) -> None:
        plan = _plan()
        driver = ScriptedDriver(
            [_missing(), OSError("read failed")],
            [ResourceApplyReceipt(remote_id="firewall-1")],
        )
        cluster = ClusterConfig()

        result = execute_resource_plan(
            plan,
            cluster,
            {"firewall_rule": driver},
            persist=lambda _state: None,
            clock=_clock,
        )

        assert result.results[0].status == "unknown"
        assert next(iter(cluster.action_checkpoints.values())).status == "unknown"


class TestPendingPlanSafety:
    def test_different_plan_cannot_replace_unknown_pending_action(self) -> None:
        plan = _plan()
        cluster = ClusterConfig(
            pending_generation=4,
            pending_plan_hash=_HASH_A,
            action_checkpoints={
                "unknown": ActionCheckpoint(
                    idempotency_key="unknown",
                    resource_id="firewall:old",
                    expected_hash=_HASH_B,
                    generation=4,
                    status="unknown",
                )
            },
        )

        with pytest.raises(PendingPlanConflictError, match="unresolved"):
            execute_resource_plan(
                plan,
                cluster,
                {},
                persist=lambda _state: None,
                clock=_clock,
            )
