"""Retire resources that left the reviewed plan, once the new plan has converged.

Every kind that can leave a plan is either deleted by its driver's idempotent
``retire()`` (absent counts as done) or explicitly forgotten because its state
lives somewhere that is already reconciled or must never be undone. Kinds owned
by teardown are refused before any state is written.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from itertools import groupby
from typing import Any, Protocol, runtime_checkable

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler.models import DeploymentTarget, ResourcePlan, canonical_hash

# Dependents first, so nothing is deleted while something that uses it remains:
# users before the squads they join, Hosts before the Inbounds they publish,
# panel Nodes before their Profile, and server runtimes before the listeners,
# firewall openings, and baseline beneath them.
RETIREMENT_ORDER: tuple[str, ...] = (
    "access_user",
    "service_user",
    "host",
    "external_squad",
    "internal_squad",
    "inbound",
    "node_binding",
    "config_profile",
    "routing_gateway",
    "egress_pool",
    "route_rule",
    "node_runtime",
    "realm_hop",
    "nginx_artifact",
    "firewall_rule",
    "certificate",
    "probe",
    "server_baseline",
)

# Kinds whose binding is dropped without a remote call, and why.
FORGET_ONLY: Mapping[str, str] = {
    # Inbounds and routing projections are materialized inside their config
    # Profile: either the surviving Profile was already rewritten without them
    # during convergence, or the whole Profile is deleted by its own retirement.
    "inbound": "lives inside its config Profile",
    "routing_gateway": "lives inside its config Profile",
    "egress_pool": "lives inside its config Profile",
    "route_rule": "lives inside its config Profile",
    # Probes are read-only checks with nothing to delete.
    "probe": "read-only check",
    # Certificates stay on disk: they are harmless, and teardown removes them.
    "certificate": "left on disk for teardown",
    # The baseline installs packages and hardens SSH/UFW. Undoing that could lock
    # the operator out or break unrelated services, so it is never reverted.
    "server_baseline": "packages and SSH/firewall hardening are never reverted",
}


# Kinds whose remote state lives on one server. When the plan keeps such a
# resource but places it on another server (`node replace`), the old placement
# is retired on the server its binding recorded. Panel-side kinds keep their
# UUID across the move and are updated in place instead.
SERVER_PLACED: frozenset[str] = frozenset(
    {"node_runtime", "realm_hop", "nginx_artifact", "firewall_rule", "certificate"}
)


@runtime_checkable
class RetiringDriver(Protocol):
    def retire(self, binding: ManagedResourceBinding) -> None: ...


def retired_bindings(plan: ResourcePlan, cluster: ClusterConfig) -> list[ManagedResourceBinding]:
    """Active bindings that left the plan or moved to another server, in retirement order."""
    desired = {
        resource.logical_id: str(getattr(resource.payload, "server_ref", "") or "") for resource in plan.resources
    }
    vanished = [
        binding
        for binding in cluster.managed_bindings.values()
        if binding.active and (binding.logical_id not in desired or _moved(binding, desired[binding.logical_id]))
    ]
    rank = {kind: index for index, kind in enumerate(RETIREMENT_ORDER)}
    return sorted(vanished, key=lambda item: (rank.get(item.resource_kind, len(rank)), item.logical_id))


def _moved(binding: ManagedResourceBinding, server_ref: str) -> bool:
    return (
        binding.resource_kind in SERVER_PLACED
        and bool(binding.server_ref)
        and bool(server_ref)
        and binding.server_ref != server_ref
    )


def unretirable(bindings: list[ManagedResourceBinding]) -> list[str]:
    return sorted(binding.logical_id for binding in bindings if binding.resource_kind not in RETIREMENT_ORDER)


def retiring_server_refs(bindings: Iterable[ManagedResourceBinding]) -> set[str]:
    """Servers that retirement must reach over SSH, including ones no longer in the intent."""
    return {
        binding.server_ref for binding in bindings if binding.server_ref and binding.resource_kind not in FORGET_ONLY
    }


def retirement_review_hash(
    plan_hash: str,
    bindings: list[ManagedResourceBinding],
    server_targets: Mapping[str, DeploymentTarget],
) -> str:
    """Bind the plan hash to the exact retirement set and the servers it touches.

    Without retirements the review hash is the plan hash, so ordinary reviews
    are unchanged. The plan hash itself cannot carry this set: it is persisted
    as the active generation and must not change once the retirements are done.
    """
    if not bindings:
        return plan_hash
    return canonical_hash(
        {
            "schema": "meridian.retirement-review/v1",
            "plan_hash": plan_hash,
            "retiring": [
                {
                    "logical_id": binding.logical_id,
                    "resource_kind": binding.resource_kind,
                    "generation": binding.generation,
                    "remote_id": binding.remote_id,
                    "server_ref": binding.server_ref,
                }
                for binding in bindings
            ],
            "server_targets": {
                server_ref: target.model_dump(mode="json") for server_ref, target in sorted(server_targets.items())
            },
        }
    )


def retire(
    bindings: list[ManagedResourceBinding],
    drivers: Mapping[Any, object],
    cluster: ClusterConfig,
    persist: Callable[[ClusterConfig], None],
) -> tuple[list[str], list[str]]:
    """Retire bindings rank by rank; return (retired logical ids, errors).

    Every binding of one rank is attempted, but a failed rank stops the later
    ones: those are what the failed resources still depend on. Retired bindings
    are deactivated and persisted one by one, so a retry resumes where this
    run stopped.
    """
    retired: list[str] = []
    errors: list[str] = []
    rank = {kind: index for index, kind in enumerate(RETIREMENT_ORDER)}
    groups = groupby(bindings, key=lambda item: rank.get(item.resource_kind, len(rank)))
    for _rank, group in groups:
        members = list(group)
        if errors:
            errors.extend(f"retire {binding.logical_id}: skipped until earlier removals succeed" for binding in members)
            continue
        for binding in members:
            try:
                _retire_one(binding, drivers.get(binding.resource_kind))
            except Exception as exc:  # noqa: BLE001 - every failure in a rank is reported
                errors.append(f"retire {binding.logical_id}: {exc}")
                continue
            binding.active = False
            retired.append(binding.logical_id)
            persist(cluster)
    return retired, errors


def _retire_one(binding: ManagedResourceBinding, driver: object | None) -> None:
    if binding.resource_kind in FORGET_ONLY:
        return
    if not isinstance(driver, RetiringDriver):
        raise RuntimeError(f"no retirement driver registered for {binding.resource_kind}")
    driver.retire(binding)
