"""Retirement ordering, forget-only kinds, failure containment, and the reviewed retirement hash."""

from __future__ import annotations

import pytest

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler.models import DeploymentTarget, ResourcePlan
from meridian.core.resources import APPLIED_KINDS
from meridian.reconciler.resource_executor import validate_resource_plan_transition
from meridian.reconciler.resources import UnsupportedResourceRetirementError
from meridian.reconciler.retirement import (
    FORGET_ONLY,
    RETIREMENT_ORDER,
    forget_server,
    retire,
    retired_bindings,
    retirement_review_hash,
    retiring_server_refs,
)
from tests.support.topology import firewall_resource, resource_plan

TEARDOWN_OWNED = {"control_service", "service_settings"}


def _plan() -> ResourcePlan:
    return resource_plan(firewall_resource(server_ref="srv-a"))


def _binding(kind: str, name: str = "gone", *, server_ref: str = "", active: bool = True) -> ManagedResourceBinding:
    return ManagedResourceBinding(
        logical_id=f"{kind}:{name}",
        resource_kind=kind,
        generation=1,
        remote_id=f"{kind}-remote",
        active=active,
        server_ref=server_ref,
    )


def _cluster(*bindings: ManagedResourceBinding) -> ClusterConfig:
    return ClusterConfig(
        managed_bindings={f"{binding.logical_id}@{binding.generation}": binding for binding in bindings},
        active_generation=1,
        active_plan_hash="b" * 64,
    )


class _Recorder:
    def __init__(self, events: list[str], *, fail_on: str = "") -> None:
        self.events = events
        self.fail_on = fail_on

    def retire(self, binding: ManagedResourceBinding) -> None:
        self.events.append(binding.logical_id)
        if binding.logical_id == self.fail_on:
            raise OSError("server unreachable")


def test_every_applied_kind_except_teardown_owned_ones_can_be_retired() -> None:
    assert set(RETIREMENT_ORDER) == APPLIED_KINDS - TEARDOWN_OWNED
    assert len(RETIREMENT_ORDER) == len(set(RETIREMENT_ORDER))
    assert set(FORGET_ONLY) <= set(RETIREMENT_ORDER)


@pytest.mark.parametrize(
    ("first", "then"),
    [
        ("control_user", "node_workload"),
        ("client_endpoint", "node_workload"),
        ("node_workload", "agent_runtime"),
        ("agent_runtime", "nginx_artifact"),
        ("realm_hop", "nginx_artifact"),
        ("realm_hop", "firewall_rule"),
        ("nginx_artifact", "firewall_rule"),
        ("firewall_rule", "certificate"),
    ],
)
def test_dependents_retire_before_what_they_use(first: str, then: str) -> None:
    assert RETIREMENT_ORDER.index(first) < RETIREMENT_ORDER.index(then)
    assert RETIREMENT_ORDER[-1] == "server_baseline"


def test_vanished_bindings_are_sorted_in_retirement_order() -> None:
    desired = _binding("firewall_rule")
    desired.logical_id = "firewall:srv-a:tcp:443"
    cluster = _cluster(
        desired,
        _binding("server_baseline"),
        _binding("realm_hop"),
        _binding("agent_runtime"),
        _binding("node_workload"),
        _binding("control_user"),
        _binding("client_endpoint", active=False),  # already retired
    )

    assert [binding.resource_kind for binding in retired_bindings(_plan(), cluster)] == [
        "control_user",
        "node_workload",
        "agent_runtime",
        "realm_hop",
        "server_baseline",
    ]


def test_what_an_uncommitted_apply_installed_retires_too() -> None:
    """An exit whose first apply failed kept an enrolled agent and open firewall rules after its removal."""
    failed = [
        _binding("agent_runtime", "exit-2", server_ref="srv-b", active=False),
        _binding("firewall_rule", "srv-b:tcp:443", server_ref="srv-b", active=False),
    ]
    for binding in failed:
        binding.generation = 2  # the active generation is 1
    stale = _binding("realm_hop", "old", active=False)  # retired before generation 1 committed
    committed = _binding("agent_runtime", "exit-2", server_ref="srv-b")  # the same agent, committed earlier

    retiring = retired_bindings(_plan(), _cluster(*failed, stale, committed))

    assert [(binding.logical_id, binding.generation) for binding in retiring] == [
        ("agent_runtime:exit-2", 2),
        ("firewall_rule:srv-b:tcp:443", 2),
    ]


@pytest.mark.parametrize(
    "kind",
    ["node_workload", "agent_runtime", "client_endpoint", "realm_hop", "control_user"],
)
def test_executor_accepts_plans_that_remove_exits_relays_and_gateways(kind: str) -> None:
    validate_resource_plan_transition(_plan(), _cluster(_binding(kind)))


@pytest.mark.parametrize("kind", sorted(TEARDOWN_OWNED))
def test_executor_still_refuses_teardown_owned_kinds(kind: str) -> None:
    with pytest.raises(UnsupportedResourceRetirementError, match=f"{kind}:gone"):
        validate_resource_plan_transition(_plan(), _cluster(_binding(kind)))


def test_a_panel_kind_left_in_a_fleet_is_refused_rather_than_retired() -> None:
    """Only `meridian migrate` forgets a Remnawave fleet's panel-side bindings."""
    with pytest.raises(UnsupportedResourceRetirementError, match="host:gone"):
        validate_resource_plan_transition(_plan(), _cluster(_binding("host")))


def test_forget_only_kinds_are_deactivated_without_a_remote_call() -> None:
    events: list[str] = []
    bindings = [_binding(kind) for kind in FORGET_ONLY]
    cluster = _cluster(*bindings)
    recorder = _Recorder(events)

    retired, errors = retire(bindings, dict.fromkeys(RETIREMENT_ORDER, recorder), cluster, lambda _state: None)

    assert errors == []
    assert retired == [binding.logical_id for binding in bindings]
    assert events == []
    assert not any(binding.active for binding in bindings)


def test_a_retirable_kind_without_a_retire_method_fails_closed() -> None:
    binding = _binding("realm_hop")

    retired, errors = retire([binding], {"realm_hop": object()}, _cluster(binding), lambda _state: None)

    assert retired == []
    assert errors == ["retire realm_hop:gone: no retirement driver registered for realm_hop"]
    assert binding.active


def test_a_failed_rank_stops_later_ranks_and_a_retry_resumes() -> None:
    events: list[str] = []
    bindings = retired_bindings(
        _plan(),
        _cluster(
            _binding("node_workload", "a"),
            _binding("node_workload", "b"),
            _binding("agent_runtime", server_ref="srv-b"),
            _binding("realm_hop", server_ref="srv-b"),
        ),
    )
    cluster = _cluster(*bindings)
    saves: list[int] = []
    drivers = dict.fromkeys(RETIREMENT_ORDER, _Recorder(events, fail_on="node_workload:a"))

    retired, errors = retire(bindings, drivers, cluster, lambda _state: saves.append(1))

    assert events == ["node_workload:a", "node_workload:b"]  # the whole failed rank is attempted
    assert retired == ["node_workload:b"]
    assert errors[0] == "retire node_workload:a: server unreachable"
    assert errors[1:] == [
        "retire agent_runtime:gone: skipped until earlier removals succeed",
        "retire realm_hop:gone: skipped until earlier removals succeed",
    ]
    assert len(saves) == 1

    events.clear()
    drivers = dict.fromkeys(RETIREMENT_ORDER, _Recorder(events))
    retried, errors = retire(retired_bindings(_plan(), cluster), drivers, cluster, lambda _state: None)

    assert errors == []
    assert retried == ["node_workload:a", "agent_runtime:gone", "realm_hop:gone"]


def test_retiring_servers_skip_forget_only_bindings() -> None:
    bindings = [
        _binding("realm_hop", server_ref="srv-relay"),
        _binding("server_baseline", server_ref="srv-baseline-only"),
        _binding("agent_runtime", server_ref="srv-exit"),
        _binding("control_user"),
    ]

    assert retiring_server_refs(bindings) == {"srv-relay", "srv-exit"}


def test_review_hash_is_the_plan_hash_without_retirements() -> None:
    assert retirement_review_hash("p" * 64, [], {}) == "p" * 64


def test_review_hash_covers_the_retirement_set_and_its_ssh_targets() -> None:
    target = DeploymentTarget(host="198.51.100.30", user="root", port=22)
    relay = _binding("realm_hop", server_ref="srv-relay")
    base = retirement_review_hash("p" * 64, [relay], {"srv-relay": target})

    assert base != "p" * 64
    assert base == retirement_review_hash("p" * 64, [relay], {"srv-relay": target})
    assert base != retirement_review_hash("p" * 64, [relay, _binding("control_user")], {"srv-relay": target})
    moved = DeploymentTarget(host="198.51.100.31", user="root", port=22)
    assert base != retirement_review_hash("p" * 64, [relay], {"srv-relay": moved})
    assert base != retirement_review_hash("q" * 64, [relay], {"srv-relay": target})


def test_an_exit_moved_to_another_server_retires_only_the_old_servers_placements() -> None:
    from meridian.compiler import compile_topology
    from meridian.setup.editor import add_exit_to_intent
    from meridian.setup.single_server import single_server_intent

    base = single_server_intent(None, server_ref="srv-a")
    before = add_exit_to_intent(
        base,
        server_ref="srv-b",
        title="exit-b",
        reality_sni="www.google.com",
        tls_hostname="",
    )
    moved_id = next(exit_.id for exit_ in before.exits if exit_.server_ref == "srv-b")
    after = before.model_copy(
        update={
            "exits": [
                exit_.model_copy(update={"server_ref": "srv-c"}) if exit_.id == moved_id else exit_
                for exit_ in before.exits
            ]
        }
    )
    deployed = compile_topology(before)
    bindings = [
        ManagedResourceBinding(
            logical_id=resource.logical_id,
            resource_kind=resource.payload.kind,
            generation=1,
            remote_id=f"{resource.logical_id}-remote",
            active=True,
            server_ref=str(getattr(resource.payload, "server_ref", "") or ""),
        )
        for resource in deployed.resources
    ]

    retiring = retired_bindings(compile_topology(after), _cluster(*bindings))

    assert {binding.server_ref for binding in retiring} == {"srv-b"}
    kinds = {binding.resource_kind for binding in retiring}
    # A re-enrolled agent revokes the old one's credentials, but only retirement stops its core.
    assert {"agent_runtime", "nginx_artifact", "firewall_rule"} <= kinds
    # The service's node keeps its identity and is re-addressed in place, never deleted.
    assert "node_workload" not in kinds


def test_forgetting_a_server_keeps_service_cleanup_and_other_servers() -> None:
    def binding(logical_id: str, kind: str, server_ref: str) -> ManagedResourceBinding:
        return ManagedResourceBinding(
            logical_id=logical_id, resource_kind=kind, generation=1, active=True, server_ref=server_ref
        )

    retiring = [
        binding("agent:exit-b", "agent_runtime", "srv-old"),
        binding("firewall:old:443", "firewall_rule", "srv-old"),
        binding("baseline:srv-old", "server_baseline", "srv-old"),
        binding("workload:exit-b", "node_workload", "srv-old"),
        binding("firewall:other:443", "firewall_rule", "srv-other"),
    ]
    cluster = ClusterConfig(managed_bindings={f"{item.logical_id}@1": item for item in retiring})

    forgotten = forget_server(cluster, retiring, "srv-old")

    assert forgotten == ["agent:exit-b", "baseline:srv-old", "firewall:old:443"]
    still_active = sorted(item.logical_id for item in cluster.managed_bindings.values() if item.active)
    assert still_active == ["firewall:other:443", "workload:exit-b"]


def test_forgetting_a_server_drops_what_an_uncommitted_apply_recorded_there() -> None:
    """Those bindings are already inactive, so deactivating them forgot nothing and retirement kept failing."""
    failed = _binding("agent_runtime", "exit-2", server_ref="srv-b", active=False)
    failed.generation = 2
    cluster = _cluster(failed)

    forgotten = forget_server(cluster, retired_bindings(_plan(), cluster), "srv-b")

    assert forgotten == ["agent_runtime:exit-2"]
    assert retired_bindings(_plan(), cluster) == []
