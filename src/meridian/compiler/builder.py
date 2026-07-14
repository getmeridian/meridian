"""Dependency-safe construction of finite compiler resources."""

from __future__ import annotations

from dataclasses import dataclass, field

from meridian.compiler.errors import TopologyCompileError
from meridian.compiler.models import (
    CompiledResource,
    ResourcePayload,
    ResourcePostcondition,
    make_resource,
)


@dataclass
class PlanBuilder:
    resources: dict[str, CompiledResource] = field(default_factory=dict)

    def add(
        self,
        logical_id: str,
        payload: ResourcePayload,
        *,
        dependencies: list[str] | None = None,
        postconditions: list[ResourcePostcondition] | None = None,
    ) -> str:
        if logical_id in self.resources:
            raise TopologyCompileError(f"Compiler produced duplicate logical resource {logical_id}.")
        self.resources[logical_id] = make_resource(
            logical_id,
            payload,
            dependencies=dependencies,
            postconditions=postconditions or [ResourcePostcondition(kind="exists", target_ref=logical_id)],
        )
        return logical_id

    def ordered(self) -> list[CompiledResource]:
        pending = dict(self.resources)
        emitted: list[CompiledResource] = []
        emitted_ids: set[str] = set()
        while pending:
            ready = sorted(
                logical_id for logical_id, resource in pending.items() if set(resource.dependencies) <= emitted_ids
            )
            if not ready:
                unresolved = ", ".join(sorted(pending))
                raise TopologyCompileError(
                    f"Compiled dependency graph contains a cycle or missing reference: {unresolved}."
                )
            for logical_id in ready:
                emitted.append(pending.pop(logical_id))
                emitted_ids.add(logical_id)
        return emitted
