"""Retire resources that left the reviewed plan, once the new plan has converged.

Only kinds with a safe, idempotent removal are retired; any other vanished
resource makes the transition fail before state is written.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler.models import ResourcePlan

# Dependents first: users and hosts before the inbounds and listeners they use.
RETIREMENT_ORDER: tuple[str, ...] = (
    "access_user",
    "host",
    "inbound",
    "nginx_artifact",
    "firewall_rule",
    "certificate",
    "probe",
)


@runtime_checkable
class RetiringDriver(Protocol):
    def retire(self, binding: ManagedResourceBinding) -> None: ...


def retired_bindings(plan: ResourcePlan, cluster: ClusterConfig) -> list[ManagedResourceBinding]:
    """Active bindings whose logical resource no longer appears in the plan, in retirement order."""
    desired = {resource.logical_id for resource in plan.resources}
    vanished = [
        binding for binding in cluster.managed_bindings.values() if binding.active and binding.logical_id not in desired
    ]
    rank = {kind: index for index, kind in enumerate(RETIREMENT_ORDER)}
    return sorted(vanished, key=lambda item: (rank.get(item.resource_kind, len(rank)), item.logical_id))


def unretirable(bindings: list[ManagedResourceBinding]) -> list[str]:
    return sorted(binding.logical_id for binding in bindings if binding.resource_kind not in RETIREMENT_ORDER)


def retire(
    bindings: list[ManagedResourceBinding],
    drivers: Mapping[Any, object],
    cluster: ClusterConfig,
    persist: Callable[[ClusterConfig], None],
) -> tuple[list[str], list[str]]:
    """Retire each binding; return (retired logical ids, errors). Retired bindings are deactivated."""
    retired: list[str] = []
    errors: list[str] = []
    for binding in bindings:
        driver = drivers.get(binding.resource_kind)
        try:
            if isinstance(driver, RetiringDriver):
                driver.retire(binding)
        except Exception as exc:  # noqa: BLE001 - every failure is reported, the rest still run
            errors.append(f"retire {binding.logical_id}: {exc}")
            continue
        binding.active = False
        retired.append(binding.logical_id)
        persist(cluster)
    return retired, errors
