"""Workload UUID, allocation, and Reality-key state is crash-safe."""

from __future__ import annotations

import pytest

from meridian.cluster import ClusterConfig, RealityKeyBinding, WorkloadBinding
from meridian.compiler.models import ConfigProfilePayload, InboundPayload
from meridian.reconciler.workloads import WorkloadStateError, WorkloadStateManager

_HASH_A = "a" * 64
_HASH_B = "b" * 64


def _profile(workload_id: str = "exit-a") -> ConfigProfilePayload:
    return ConfigProfilePayload(
        workload_id=workload_id,
        name=f"Meridian v4 / {workload_id}",
        inbound_refs=[
            f"inbound:{workload_id}:reality",
            f"inbound:{workload_id}:xhttp",
        ],
        inbounds=[
            InboundPayload(
                workload_ref=workload_id,
                protocol="reality",
                tag=f"meridian-{workload_id}-reality",
                listen_port=10443,
                public_port=443,
                reality_sni="www.microsoft.com",
            ),
            InboundPayload(
                workload_ref=workload_id,
                protocol="xhttp",
                tag=f"meridian-{workload_id}-xhttp",
                listen_port=30443,
                public_port=443,
                tls_sni=f"{workload_id}.example.com",
                host=f"{workload_id}.example.com",
                path=f"{workload_id}-path",
            ),
        ],
        outbound_tags=["direct"],
    )


def _keys(suffix: str = "a") -> RealityKeyBinding:
    return RealityKeyBinding(
        public_key=f"public-{suffix}",
        private_key=f"private-{suffix}",
        short_id=f"short-{suffix}",
    )


def test_allocates_complete_keys_and_persists_before_returning() -> None:
    cluster = ClusterConfig()
    generated: list[str] = []
    snapshots: list[tuple[str, str, str]] = []

    def key_factory(server_ref: str) -> RealityKeyBinding:
        generated.append(server_ref)
        return _keys()

    def persist(state: ClusterConfig) -> None:
        workload = state.workloads[0]
        keys = workload.reality_keys["srv-exit-a"]
        snapshots.append((keys.public_key, keys.private_key, keys.short_id))

    manager = WorkloadStateManager(cluster, persist=persist, key_factory=key_factory)
    workload = manager.ensure(
        _profile(),
        generation=1,
        desired_hash=_HASH_A,
        server_ref="srv-exit-a",
    )

    assert generated == ["srv-exit-a"]
    assert snapshots == [("public-a", "private-a", "short-a")]
    assert workload.reality_keys["srv-exit-a"] == _keys()
    assert cluster.allocations["inbound:exit-a:reality"].port == 10443
    assert cluster.allocations["inbound:exit-a:xhttp"].path == "exit-a-path"


def test_new_generation_reuses_complete_keys_without_calling_factory() -> None:
    cluster = ClusterConfig(
        workloads=[
            WorkloadBinding(
                id="exit-a",
                generation=1,
                active=True,
                server_refs=["srv-exit-a"],
                reality_keys={"srv-exit-a": _keys()},
                desired_hash=_HASH_A,
            )
        ],
        active_generation=1,
    )

    def unexpected_factory(_server_ref: str) -> RealityKeyBinding:
        raise AssertionError("existing Reality keys must be reused")

    manager = WorkloadStateManager(
        cluster,
        persist=lambda _state: None,
        key_factory=unexpected_factory,
    )
    workload = manager.ensure(
        _profile(),
        generation=2,
        desired_hash=_HASH_B,
        server_ref="srv-exit-a",
    )

    assert workload.reality_keys["srv-exit-a"] == _keys()
    assert workload.reality_keys["srv-exit-a"] is not cluster.workloads[0].reality_keys["srv-exit-a"]


def test_partial_predecessor_keys_fail_closed_instead_of_rotating() -> None:
    cluster = ClusterConfig(
        workloads=[
            WorkloadBinding(
                id="exit-a",
                generation=1,
                reality_keys={
                    "srv-exit-a": RealityKeyBinding(
                        public_key="public",
                        private_key="",
                        short_id="short",
                    )
                },
            )
        ]
    )
    calls = 0

    def key_factory(_server_ref: str) -> RealityKeyBinding:
        nonlocal calls
        calls += 1
        return _keys()

    manager = WorkloadStateManager(cluster, persist=lambda _state: None, key_factory=key_factory)

    with pytest.raises(WorkloadStateError, match="incomplete"):
        manager.ensure(
            _profile(),
            generation=2,
            desired_hash=_HASH_B,
            server_ref="srv-exit-a",
        )

    assert calls == 0


def test_two_exit_uuid_bindings_remain_independent() -> None:
    cluster = ClusterConfig()
    generated = {
        "srv-exit-a": _keys("a"),
        "srv-exit-b": _keys("b"),
    }
    manager = WorkloadStateManager(
        cluster,
        persist=lambda _state: None,
        key_factory=lambda server_ref: generated[server_ref],
    )
    manager.ensure(
        _profile("exit-a"),
        generation=1,
        desired_hash=_HASH_A,
        server_ref="srv-exit-a",
    )
    manager.ensure(
        _profile("exit-b"),
        generation=1,
        desired_hash=_HASH_B,
        server_ref="srv-exit-b",
    )

    manager.record_profile_uuid("exit-a", 1, "profile-a")
    manager.record_profile_uuid("exit-b", 1, "profile-b")
    manager.record_inbound_uuid("exit-a", 1, "inbound:exit-a:reality", "inbound-a")
    manager.record_inbound_uuid("exit-b", 1, "inbound:exit-b:reality", "inbound-b")
    manager.record_node_uuid("exit-a", 1, "srv-exit-a", "node-a")
    manager.record_node_uuid("exit-b", 1, "srv-exit-b", "node-b")
    manager.record_host_uuid("exit-a", 1, "host:exit-a:reality:direct", "host-a")
    manager.record_host_uuid("exit-b", 1, "host:exit-b:reality:direct", "host-b")

    exit_a = manager.require("exit-a", 1)
    exit_b = manager.require("exit-b", 1)
    assert exit_a.config_profile_uuid == "profile-a"
    assert exit_b.config_profile_uuid == "profile-b"
    assert exit_a.reality_keys["srv-exit-a"] == _keys("a")
    assert exit_b.reality_keys["srv-exit-b"] == _keys("b")
    assert exit_a.inbound_uuids == {"inbound:exit-a:reality": "inbound-a"}
    assert exit_b.inbound_uuids == {"inbound:exit-b:reality": "inbound-b"}
    assert exit_a.host_uuids == {"host:exit-a:reality:direct": "host-a"}
    assert exit_b.host_uuids == {"host:exit-b:reality:direct": "host-b"}
