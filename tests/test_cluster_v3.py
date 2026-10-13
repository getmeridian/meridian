"""Cluster schema V3 workload bindings and conservative V2 adoption."""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.cluster import (
    ActionCheckpoint,
    ClusterConfig,
    ManagedResourceBinding,
    RealityKeyBinding,
    ResourceAllocation,
    WorkloadBinding,
)
from meridian.core.errors import LocalStateCorruptedError
from meridian.core.topology import (
    SetupIntent,
)
from tests.support.topology import reality_path, single_exit_intent

_PROFILE_UUID = "550e8400-e29b-41d4-a716-446655440000"
_NODE_UUID = "660e8400-e29b-41d4-a716-446655440001"
_INBOUND_UUID = "770e8400-e29b-41d4-a716-446655440002"
_HOST_UUID = "880e8400-e29b-41d4-a716-446655440003"
_SQUAD_UUID = "990e8400-e29b-41d4-a716-446655440004"
_HASH_A = "a" * 64
_HASH_B = "b" * 64


def _intent() -> SetupIntent:
    return single_exit_intent(paths=[reality_path("reality", "www.microsoft.com")])


class TestV3RoundTrip:
    def test_topology_bindings_allocations_and_checkpoints_round_trip(self, tmp_path: Path) -> None:
        cluster = ClusterConfig(
            topology_intent=_intent(),
            workloads=[
                WorkloadBinding(
                    id="exit-a",
                    generation=2,
                    active=True,
                    server_refs=["srv-exit"],
                    config_profile_uuid=_PROFILE_UUID,
                    node_uuids={"srv-exit": _NODE_UUID},
                    inbound_uuids={"reality": _INBOUND_UUID},
                    reality_keys={
                        "srv-exit": RealityKeyBinding(
                            public_key="public",
                            private_key="private",
                            short_id="abcdef0123456789",
                        )
                    },
                    desired_hash=_HASH_A,
                )
            ],
            managed_bindings={
                "profile:exit-a@2": ManagedResourceBinding(
                    logical_id="profile:exit-a",
                    resource_kind="config_profile",
                    generation=2,
                    remote_id=_PROFILE_UUID,
                    desired_hash=_HASH_A,
                    observed_hash=_HASH_A,
                    active=True,
                )
            },
            allocations={
                "inbound:exit-a:reality": ResourceAllocation(
                    logical_id="inbound:exit-a:reality",
                    server_ref="srv-exit",
                    transport="tcp",
                    port=10443,
                    tag="meridian-exit-a-reality",
                )
            },
            action_checkpoints={
                "checkpoint-a": ActionCheckpoint(
                    idempotency_key="checkpoint-a",
                    resource_id="profile:exit-a",
                    expected_hash=_HASH_A,
                    generation=2,
                    status="succeeded",
                    attempts=1,
                    observed_hash=_HASH_A,
                    remote_id=_PROFILE_UUID,
                    updated_at="2026-07-14T15:00:00Z",
                )
            },
            active_generation=2,
            active_plan_hash=_HASH_B,
        )
        path = tmp_path / "cluster.yml"

        cluster.save(path)
        loaded = ClusterConfig.load(path)

        assert loaded.topology_intent == cluster.topology_intent
        assert loaded.workloads == cluster.workloads
        assert loaded.managed_bindings == cluster.managed_bindings
        assert loaded.allocations == cluster.allocations
        assert loaded.action_checkpoints == cluster.action_checkpoints
        assert loaded.active_generation == 2
        assert loaded.active_plan_hash == _HASH_B

    def test_old_generation_survives_until_later_garbage_collection(self, tmp_path: Path) -> None:
        cluster = ClusterConfig(
            workloads=[
                WorkloadBinding(id="exit-a", generation=1, active=False, adopted=True),
                WorkloadBinding(id="exit-a", generation=2, active=True),
            ]
        )
        path = tmp_path / "cluster.yml"

        cluster.save(path)
        loaded = ClusterConfig.load(path)

        assert [(item.generation, item.active) for item in loaded.workloads] == [(1, False), (2, True)]

    def test_invalid_binding_shape_fails_closed(self, tmp_path: Path) -> None:
        path = tmp_path / "cluster.yml"
        path.write_text("version: 3\nmanaged_bindings:\n  broken: not-a-mapping\n")

        with pytest.raises(LocalStateCorruptedError, match=r"managed_bindings\[broken\] must be a mapping"):
            ClusterConfig.load(path)
