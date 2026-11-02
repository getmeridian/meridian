"""Address-free host capacity, sent only with `capacity_telemetry` negotiated.

Null means collection is unavailable, never zero. Network counters start at
zero for each epoch, rather than sending Linux's potentially unsafe uint64
values. Epochs change on collector restart, interface change, reset, wrap, or
the JSON safe-integer ceiling. Rates describe adjacent monotonic samples only.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, model_validator

from meridian.core.agent_types import AgentModel, Count, CounterEpoch, InboundTag, Timestamp

CpuCount = Annotated[int, Field(ge=1, le=65_536)]


class NetworkCapacity(AgentModel):
    """Host traffic on the default-route interface; includes overhead and non-VPN traffic.

    No interface name, address, or device identifier leaves the host. The first
    sample of an epoch has zero counters and null rates. A missing or overlong
    interval also has null rates; it must not manufacture a current speed.
    """

    counter_epoch: CounterEpoch
    rx_bytes: Count
    tx_bytes: Count
    rx_bytes_per_second: Count | None
    tx_bytes_per_second: Count | None
    interval_millis: int | None = Field(ge=1, le=180_000)

    @model_validator(mode="after")
    def interval_matches_rates(self) -> NetworkCapacity:
        available = self.interval_millis is not None
        if (self.rx_bytes_per_second is not None) != available or (self.tx_bytes_per_second is not None) != available:
            raise ValueError("Network rates need one valid collection interval.")
        return self


class ProcessCapacity(AgentModel):
    """The managed Xray process; missing, changed, or unreadable evidence is null."""

    uptime_seconds: Count | None
    memory_resident_bytes: Count | None


class InboundConnections(AgentModel):
    """Aggregate only; never derived by enumerating client addresses or identities."""

    inbound_tag: InboundTag
    connections: Count


class NodeCapacity(AgentModel):
    """One collection, identified independently of delivery retries and wall-clock skew."""

    collection_epoch: CounterEpoch
    sequence: int = Field(ge=1, le=2**53 - 1)
    collected_at: Timestamp
    cpu_count: CpuCount | None
    load1_milli: Count | None
    load5_milli: Count | None
    load15_milli: Count | None
    memory_total_bytes: Count | None
    memory_available_bytes: Count | None
    network: NetworkCapacity | None
    xray: ProcessCapacity | None
    inbound_connections: list[InboundConnections] | None = Field(
        max_length=1024,
        description="Null on the pinned core, which has no address-free aggregate inbound connection counter.",
    )

    @model_validator(mode="after")
    def coherent_capacity(self) -> NodeCapacity:
        if (
            self.memory_total_bytes is not None
            and self.memory_available_bytes is not None
            and self.memory_available_bytes > self.memory_total_bytes
        ):
            raise ValueError("Available memory cannot exceed total memory.")
        if self.inbound_connections is not None:
            tags = [entry.inbound_tag for entry in self.inbound_connections]
            if len(tags) != len(set(tags)):
                raise ValueError("Report each inbound connection count once.")
        return self
