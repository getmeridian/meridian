"""Cluster schema V3 topology intent, workload bindings, and wire conversion."""

from __future__ import annotations

from dataclasses import MISSING, dataclass, fields, is_dataclass
from typing import Any

from meridian.cluster import (
    ActionCheckpoint,
    ClusterConfig,
    ManagedResourceBinding,
    RealityKeyBinding,
    ResourceAllocation,
    WorkloadBinding,
)
from meridian.core.topology import SetupIntent


@dataclass(frozen=True)
class LoadedClusterV3:
    topology_intent: SetupIntent | None
    workloads: list[WorkloadBinding]
    managed_bindings: dict[str, ManagedResourceBinding]
    allocations: dict[str, ResourceAllocation]
    action_checkpoints: dict[str, ActionCheckpoint]
    active_generation: int
    active_plan_hash: str
    pending_generation: int
    pending_plan_hash: str


def serialize_cluster_v3(config: ClusterConfig) -> dict[str, Any]:
    """Serialize only V3-owned fields, omitting empty state."""
    output: dict[str, Any] = {}
    if config.topology_intent is not None:
        output["topology_intent"] = config.topology_intent.model_dump(mode="json", by_alias=True)
    if config.workloads:
        output["workloads"] = [_serialize_dataclass(workload) for workload in config.workloads]
    if config.managed_bindings:
        output["managed_bindings"] = {
            key: _serialize_dataclass(binding) for key, binding in sorted(config.managed_bindings.items())
        }
    if config.allocations:
        output["allocations"] = {
            key: _serialize_dataclass(allocation) for key, allocation in sorted(config.allocations.items())
        }
    if config.action_checkpoints:
        output["action_checkpoints"] = {
            key: _serialize_dataclass(checkpoint) for key, checkpoint in sorted(config.action_checkpoints.items())
        }
    if config.active_generation:
        output["active_generation"] = config.active_generation
    if config.active_plan_hash:
        output["active_plan_hash"] = config.active_plan_hash
    if config.pending_generation:
        output["pending_generation"] = config.pending_generation
    if config.pending_plan_hash:
        output["pending_plan_hash"] = config.pending_plan_hash
    return output


def load_cluster_v3(data: dict[str, Any]) -> LoadedClusterV3:
    """Load the topology intent and its durable compiled-resource state."""
    topology_intent = None
    if data.get("topology_intent") is not None:
        topology_intent = SetupIntent.model_validate(data["topology_intent"])
    workloads = _load_workloads(data.get("workloads", []))
    managed_bindings = _load_map(
        data.get("managed_bindings", {}),
        ManagedResourceBinding,
    )
    allocations = _load_map(data.get("allocations", {}), ResourceAllocation)
    checkpoints = _load_map(data.get("action_checkpoints", {}), ActionCheckpoint)

    return LoadedClusterV3(
        topology_intent=topology_intent,
        workloads=workloads,
        managed_bindings=managed_bindings,
        allocations=allocations,
        action_checkpoints=checkpoints,
        active_generation=int(data.get("active_generation", 0)),
        active_plan_hash=str(data.get("active_plan_hash", "")),
        pending_generation=int(data.get("pending_generation", 0)),
        pending_plan_hash=str(data.get("pending_plan_hash", "")),
    )


def validate_cluster_v3_structure(data: dict[str, Any]) -> None:
    """Reject malformed V3 containers before dataclass coercion."""
    topology = data.get("topology_intent")
    if topology is not None and not isinstance(topology, dict):
        raise ValueError(f"topology_intent must be a mapping or null, got {type(topology).__name__}")

    workloads = data.get("workloads", [])
    if not isinstance(workloads, list):
        raise ValueError(f"workloads must be a list, got {type(workloads).__name__}")
    for index, workload in enumerate(workloads):
        if not isinstance(workload, dict):
            raise ValueError(f"workloads[{index}] must be a mapping, got {type(workload).__name__}")
        reality_keys = workload.get("reality_keys", {})
        if not isinstance(reality_keys, dict):
            raise ValueError(f"workloads[{index}].reality_keys must be a mapping")
        for server_ref, key_material in reality_keys.items():
            if not isinstance(server_ref, str) or not isinstance(key_material, dict):
                raise ValueError(f"workloads[{index}].reality_keys entries must be string-to-mapping values")

    for key in ("managed_bindings", "allocations", "action_checkpoints"):
        _require_mapping_values(data, key)
    for key in ("active_generation", "pending_generation"):
        value = data.get(key, 0)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{key} must be an integer, got {type(value).__name__}")
    for key in ("active_plan_hash", "pending_plan_hash"):
        value = data.get(key, "")
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string, got {type(value).__name__}")


def _serialize_dataclass(value: Any) -> dict[str, Any]:
    output = {
        item.name: _serialize_value(getattr(value, item.name))
        for item in fields(value)
        if not item.name.startswith("_") and getattr(value, item.name) is not None
    }
    extra = getattr(value, "_extra", {})
    if isinstance(extra, dict):
        for key, item in extra.items():
            output.setdefault(key, _serialize_value(item))
    return output


def _serialize_value(value: Any) -> Any:
    if is_dataclass(value):
        return _serialize_dataclass(value)
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    return value


def _load_dataclass(raw: Any, cls: type[Any], *, transforms: dict[str, Any] | None = None) -> Any:
    if not isinstance(raw, dict):
        raw = {}
    transforms = transforms or {}
    public_fields = {item.name for item in fields(cls) if not item.name.startswith("_")}
    defaults: dict[str, Any] = {}
    for item in fields(cls):
        if item.name.startswith("_"):
            continue
        if item.default is not MISSING:
            defaults[item.name] = item.default

    values: dict[str, Any] = {}
    for field_name in public_fields:
        if field_name not in raw:
            continue
        value = raw[field_name]
        if value is None and field_name in defaults:
            value = defaults[field_name]
        if field_name in transforms:
            value = transforms[field_name](value)
        values[field_name] = value
    extra = {key: value for key, value in raw.items() if key not in public_fields}
    return cls(**values, _extra=extra)


def _load_workloads(raw: Any) -> list[WorkloadBinding]:
    if not isinstance(raw, list):
        return []
    return [
        _load_dataclass(
            item,
            WorkloadBinding,
            transforms={"reality_keys": _load_reality_keys},
        )
        for item in raw
    ]


def _load_reality_keys(raw: Any) -> dict[str, RealityKeyBinding]:
    if not isinstance(raw, dict):
        return {}
    return {str(server_ref): _load_dataclass(value, RealityKeyBinding) for server_ref, value in raw.items()}


def _load_map(raw: Any, cls: type[Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): _load_dataclass(value, cls) for key, value in raw.items()}


def _require_mapping_values(data: dict[str, Any], key: str) -> None:
    value = data.get(key)
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping, got {type(value).__name__}")
    for item_key, item in value.items():
        if not isinstance(item_key, str):
            raise ValueError(f"{key} keys must be strings")
        if not isinstance(item, dict):
            raise ValueError(f"{key}[{item_key}] must be a mapping, got {type(item).__name__}")
