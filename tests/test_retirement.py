"""Retirement ordering, forget-only kinds, failure containment, and the reviewed retirement hash."""

from __future__ import annotations

from typing import get_args

import pytest

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler.models import (
    DeploymentTarget,
    ResourceKind,
    ResourcePlan,
)
from meridian.reconciler.resource_executor import validate_resource_plan_transition
from meridian.reconciler.resources import UnsupportedResourceRetirementError
from meridian.reconciler.retirement import (
    FORGET_ONLY,
    RETIREMENT_ORDER,
    retire,
    retired_bindings,
    retirement_review_hash,
    retiring_server_refs,
)
from tests.support.topology import firewall_resource, resource_plan

TEARDOWN_OWNED = {"control_plane_runtime", "subscription_template", "subscription_settings"}


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


def test_every_kind_except_teardown_owned_ones_can_be_retired() -> None:
    assert set(RETIREMENT_ORDER) == set(get_args(ResourceKind)) - TEARDOWN_OWNED
    assert len(RETIREMENT_ORDER) == len(set(RETIREMENT_ORDER))
    assert set(FORGET_ONLY) <= set(RETIREMENT_ORDER)


@pytest.mark.parametrize(
    ("first", "then"),
    [
        ("access_user", "external_squad"),
        ("access_user", "internal_squad"),
        ("service_user", "internal_squad"),
        ("host", "inbound"),
        ("host", "config_profile"),
        ("internal_squad", "config_profile"),
        ("node_binding", "config_profile"),
        ("node_binding", "node_runtime"),
        ("config_profile", "node_runtime"),
        ("node_runtime", "nginx_artifact"),
        ("realm_hop", "nginx_artifact"),
        ("realm_hop", "firewall_rule"),
        ("nginx_artifact", "firewall_rule"),
        ("firewall_rule", "certificate"),
        ("probe", "server_baseline"),
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
        _binding("config_profile"),
        _binding("node_binding"),
        _binding("access_user"),
        _binding("host", active=False),  # already retired
    )

    assert [binding.resource_kind for binding in retired_bindings(_plan(), cluster)] == [
        "access_user",
        "node_binding",
        "config_profile",
        "realm_hop",
        "server_baseline",
    ]


@pytest.mark.parametrize(
    "kind",
    ["node_binding", "node_runtime", "config_profile", "realm_hop", "service_user", "internal_squad", "egress_pool"],
)
def test_executor_accepts_plans_that_remove_exits_relays_and_gateways(kind: str) -> None:
    validate_resource_plan_transition(_plan(), _cluster(_binding(kind)))


@pytest.mark.parametrize("kind", sorted(TEARDOWN_OWNED))
def test_executor_still_refuses_teardown_owned_kinds(kind: str) -> None:
    with pytest.raises(UnsupportedResourceRetirementError, match=f"{kind}:gone"):
        validate_resource_plan_transition(_plan(), _cluster(_binding(kind)))


def test_forget_only_kinds_are_deactivated_without_a_remote_call() -> None:
    events: list[str] = []
    kinds = ("inbound", "egress_pool", "route_rule", "routing_gateway", "server_baseline")
    bindings = [_binding(kind) for kind in kinds]
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
            _binding("node_binding", "a"),
            _binding("node_binding", "b"),
            _binding("config_profile"),
            _binding("node_runtime", server_ref="srv-b"),
        ),
    )
    cluster = _cluster(*bindings)
    saves: list[int] = []
    drivers = dict.fromkeys(RETIREMENT_ORDER, _Recorder(events, fail_on="node_binding:a"))

    retired, errors = retire(bindings, drivers, cluster, lambda _state: saves.append(1))

    assert events == ["node_binding:a", "node_binding:b"]  # the whole failed rank is attempted
    assert retired == ["node_binding:b"]
    assert errors[0] == "retire node_binding:a: server unreachable"
    assert errors[1:] == [
        "retire config_profile:gone: skipped until earlier removals succeed",
        "retire node_runtime:gone: skipped until earlier removals succeed",
    ]
    assert len(saves) == 1

    events.clear()
    drivers = dict.fromkeys(RETIREMENT_ORDER, _Recorder(events))
    retried, errors = retire(retired_bindings(_plan(), cluster), drivers, cluster, lambda _state: None)

    assert errors == []
    assert retried == ["node_binding:a", "config_profile:gone", "node_runtime:gone"]


def test_retiring_servers_skip_forget_only_bindings() -> None:
    bindings = [
        _binding("realm_hop", server_ref="srv-relay"),
        _binding("server_baseline", server_ref="srv-baseline-only"),
        _binding("node_binding", server_ref="srv-exit"),
        _binding("host"),
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
    assert base != retirement_review_hash("p" * 64, [relay, _binding("host")], {"srv-relay": target})
    moved = DeploymentTarget(host="198.51.100.31", user="root", port=22)
    assert base != retirement_review_hash("p" * 64, [relay], {"srv-relay": moved})
    assert base != retirement_review_hash("q" * 64, [relay], {"srv-relay": target})


def test_an_exit_moved_to_another_server_retires_only_the_old_servers_placements() -> None:
    from meridian.compiler import compile_topology
    from meridian.setup.editor import add_exit_to_intent
    from meridian.setup.single_server import single_server_intent

    before = add_exit_to_intent(
        single_server_intent(None, server_ref="srv-a"),
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
    assert "node_runtime" in kinds and "nginx_artifact" in kinds and "firewall_rule" in kinds
    # The panel Node keeps its UUID and is re-addressed in place, never deleted.
    assert "node_binding" not in kinds
