"""Drivers for reviewed resources whose mutation runs at an outer boundary."""

from __future__ import annotations

from meridian.cluster import ManagedResourceBinding
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceApplyReceipt,
    ResourceObservation,
    postcondition_key,
)


class ContractResourceDriver:
    """Checkpoint an already-satisfied runtime or later verification contract."""

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        recorded = binding is not None
        return ResourceObservation(
            exists=recorded,
            observed_hash=action.expected_hash if recorded else "",
            remote_id=(binding.remote_id if binding is not None else ""),
            satisfied_postconditions=sorted(
                {
                    postcondition_key(
                        condition.kind,
                        condition.target_ref,
                        condition.detail,
                    )
                    for condition in action.resource.postconditions
                }
            )
            if recorded
            else [],
        )

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        return ResourceApplyReceipt(
            remote_id=(binding.remote_id if binding is not None else action.resource.logical_id)
        )
