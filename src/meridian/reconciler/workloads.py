"""Crash-safe workload state and Reality key allocation for V4 resources."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

from meridian.cluster import (
    ClusterConfig,
    RealityKeyBinding,
    ResourceAllocation,
    WorkloadBinding,
)
from meridian.compiler.models import ConfigProfilePayload
from meridian.core.errors import LocalStateError
from meridian.xray_config import generate_reality_keypair

if TYPE_CHECKING:
    from meridian.ssh import ServerConnection

PersistCluster = Callable[[ClusterConfig], None]
ConnectionFactory = Callable[[str], "ServerConnection"]


class RealityKeyFactory(Protocol):
    def __call__(self, server_ref: str) -> RealityKeyBinding: ...


class WorkloadStateError(LocalStateError):
    """Workload state is incomplete or conflicts with a reviewed action."""


class WorkloadStateManager:
    """Allocate secret state before mutation and record profile-scoped UUIDs."""

    def __init__(
        self,
        cluster: ClusterConfig,
        *,
        persist: PersistCluster,
        key_factory: RealityKeyFactory | None = None,
    ) -> None:
        self.cluster = cluster
        self._persist = persist
        self._key_factory = key_factory

    def ensure(
        self,
        profile: ConfigProfilePayload,
        *,
        generation: int,
        desired_hash: str,
        server_ref: str,
    ) -> WorkloadBinding:
        workload = self.find(profile.workload_id, generation)
        if workload is not None and workload.desired_hash not in {"", desired_hash}:
            raise WorkloadStateError(
                f"Workload {profile.workload_id}@{generation} belongs to a different reviewed payload.",
                hint="Resume the original plan or start a new generation.",
            )
        if workload is None:
            workload = WorkloadBinding(
                id=profile.workload_id,
                generation=generation,
                active=self.cluster.active_generation == generation,
                server_refs=[server_ref],
                config_profile_name=profile.name,
                desired_hash=desired_hash,
            )
            self.cluster.workloads.append(workload)
        else:
            workload.server_refs = [server_ref]
            workload.config_profile_name = profile.name
            workload.desired_hash = desired_hash

        if any(inbound.protocol == "reality" for inbound in profile.inbounds):
            workload.reality_keys[server_ref] = self._ensure_reality_keys(
                workload,
                server_ref=server_ref,
            )
        self._record_allocations(profile, server_ref=server_ref)
        self.cluster.workloads.sort(key=lambda item: (item.id, item.generation))
        self._persist(self.cluster)
        return workload

    def find(self, workload_id: str, generation: int) -> WorkloadBinding | None:
        return next(
            (
                workload
                for workload in self.cluster.workloads
                if workload.id == workload_id and workload.generation == generation
            ),
            None,
        )

    def record_profile_uuid(self, workload_id: str, generation: int, profile_uuid: str) -> None:
        workload = self.require(workload_id, generation)
        workload.config_profile_uuid = profile_uuid
        self._persist(self.cluster)

    def record_inbound_uuid(
        self,
        workload_id: str,
        generation: int,
        inbound_ref: str,
        inbound_uuid: str,
    ) -> None:
        workload = self.require(workload_id, generation)
        workload.inbound_uuids[inbound_ref] = inbound_uuid
        self._persist(self.cluster)

    def record_node_uuid(
        self,
        workload_id: str,
        generation: int,
        server_ref: str,
        node_uuid: str,
    ) -> None:
        workload = self.require(workload_id, generation)
        workload.node_uuids[server_ref] = node_uuid
        self._persist(self.cluster)

    def record_host_uuid(
        self,
        workload_id: str,
        generation: int,
        host_ref: str,
        host_uuid: str,
    ) -> None:
        workload = self.require(workload_id, generation)
        workload.host_uuids[host_ref] = host_uuid
        self._persist(self.cluster)

    def require(self, workload_id: str, generation: int) -> WorkloadBinding:
        workload = self.find(workload_id, generation)
        if workload is None:
            raise WorkloadStateError(
                f"Workload state {workload_id}@{generation} is missing.",
                hint="Re-run the Profile action before dependent resources.",
            )
        return workload

    def _ensure_reality_keys(
        self,
        workload: WorkloadBinding,
        *,
        server_ref: str,
    ) -> RealityKeyBinding:
        current = workload.reality_keys.get(server_ref)
        if current is not None:
            _require_complete_keys(workload.id, current)
            return current
        predecessors = sorted(
            (
                item
                for item in self.cluster.workloads
                if item.id == workload.id and item.generation < workload.generation
            ),
            key=lambda item: item.generation,
            reverse=True,
        )
        for predecessor in predecessors:
            prior = predecessor.reality_keys.get(server_ref)
            if prior is None:
                continue
            _require_complete_keys(workload.id, prior)
            return RealityKeyBinding(
                public_key=prior.public_key,
                private_key=prior.private_key,
                short_id=prior.short_id,
            )
        if self._key_factory is None:
            raise WorkloadStateError(
                f"Reality keys have not been allocated for workload {workload.id}.",
                hint="Connect to the exit server and allocate keys before creating its Profile.",
            )
        generated = self._key_factory(server_ref)
        _require_complete_keys(workload.id, generated)
        return generated

    def _record_allocations(self, profile: ConfigProfilePayload, *, server_ref: str) -> None:
        for inbound_ref, inbound in zip(profile.inbound_refs, profile.inbounds, strict=True):
            self.cluster.allocations[inbound_ref] = ResourceAllocation(
                logical_id=inbound_ref,
                server_ref=server_ref,
                transport="udp" if inbound.protocol == "hysteria2" else "tcp",
                port=inbound.listen_port,
                path=inbound.path,
                tag=inbound.tag,
            )


class SSHRealityKeyFactory:
    """Generate one Reality key triple through the selected exit connection."""

    def __init__(self, connection_for: ConnectionFactory) -> None:
        self._connection_for = connection_for

    def __call__(self, server_ref: str) -> RealityKeyBinding:
        private_key, public_key = generate_reality_keypair(self._connection_for(server_ref))
        return RealityKeyBinding(
            public_key=public_key,
            private_key=private_key,
            short_id=secrets.token_hex(4),
        )


def _require_complete_keys(workload_id: str, keys: RealityKeyBinding) -> None:
    present = [bool(keys.public_key), bool(keys.private_key), bool(keys.short_id)]
    if not all(present):
        raise WorkloadStateError(
            f"Reality key material is incomplete for workload {workload_id}.",
            hint="Recover the public key, private key, and short ID together; Meridian will not rotate partial state.",
        )
