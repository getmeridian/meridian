"""Shared capacity evidence and bounded server history for API, CLI, and console."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from meridian.core.agent_capacity import NodeCapacity
from meridian.core.agent_types import Count, NodeId, Timestamp
from meridian.core.models import CoreModel

CAPACITY_STALE_SECONDS = 180
CAPACITY_RETENTION_HOURS = 168
CAPACITY_RECENT_SECONDS = 3600
CAPACITY_RECENT_INTERVAL_SECONDS = 10
CAPACITY_RECENT_POINTS = CAPACITY_RECENT_SECONDS // CAPACITY_RECENT_INTERVAL_SECONDS
CapacityStatus = Literal["not_reported", "unavailable", "current", "stale"]


class CapacityValues(CoreModel):
    """Integer figures; load is per CPU, memory pressure is per mille, and rates are bytes/s."""

    cpu_count: Count | None = None
    load1_milli: Count | None = None
    load5_milli: Count | None = None
    load15_milli: Count | None = None
    load_per_cpu_milli: Count | None = None
    memory_total_bytes: Count | None = None
    memory_available_bytes: Count | None = None
    memory_used_bytes: Count | None = None
    memory_used_per_mille: Count | None = None
    rx_bytes_per_second: Count | None = None
    tx_bytes_per_second: Count | None = None
    xray_memory_resident_bytes: Count | None = None
    xray_uptime_seconds: Count | None = None


class CapacityCurrent(CoreModel):
    """Freshness uses the last accepted collection's service receipt, never a hello or replay."""

    status: CapacityStatus = "not_reported"
    sample: NodeCapacity | None = None
    values: CapacityValues | None = None
    received_at: Timestamp | None = None
    age_seconds: Count | None = None


class CapacityPoint(CoreModel):
    received_at: Timestamp
    values: CapacityValues


class CapacityHour(CoreModel):
    starts_at: Timestamp
    samples: Count
    mean: CapacityValues
    peak: CapacityValues
    rx_bytes: int | None = Field(ge=0)
    tx_bytes: int | None = Field(ge=0)
    covered_seconds: Count = Field(description="Adjacent received network samples only; gaps stay unmeasured.")


class CapacityDay(CoreModel):
    """The current UTC hour and its preceding 23 buckets; partial coverage is explicit."""

    starts_at: Timestamp
    ends_at: Timestamp
    samples: Count
    mean: CapacityValues
    peak: CapacityValues
    rx_bytes: int | None = Field(ge=0)
    tx_bytes: int | None = Field(ge=0)
    covered_seconds: Count


class NodeCapacityView(CapacityCurrent):
    node_id: NodeId
    recent: list[CapacityPoint] = Field(max_length=CAPACITY_RECENT_POINTS)
    hourly: list[CapacityHour] = Field(max_length=CAPACITY_RETENTION_HOURS)
    last_day: CapacityDay
    retention_hours: int = CAPACITY_RETENTION_HOURS
    recent_window_seconds: int = CAPACITY_RECENT_SECONDS
    recent_resolution_seconds: int = CAPACITY_RECENT_INTERVAL_SECONDS


class CapacityQuery(CoreModel):
    hours: int = Field(default=24, ge=1, le=CAPACITY_RETENTION_HOURS)


def capacity_values(sample: NodeCapacity) -> CapacityValues:
    total = sample.memory_total_bytes
    available = sample.memory_available_bytes
    used = total - available if total is not None and available is not None else None
    return CapacityValues(
        cpu_count=sample.cpu_count,
        load1_milli=sample.load1_milli,
        load5_milli=sample.load5_milli,
        load15_milli=sample.load15_milli,
        load_per_cpu_milli=sample.load1_milli // sample.cpu_count
        if sample.load1_milli is not None and sample.cpu_count is not None
        else None,
        memory_total_bytes=total,
        memory_available_bytes=available,
        memory_used_bytes=used,
        memory_used_per_mille=used * 1000 // total if used is not None and total else None,
        rx_bytes_per_second=sample.network.rx_bytes_per_second if sample.network is not None else None,
        tx_bytes_per_second=sample.network.tx_bytes_per_second if sample.network is not None else None,
        xray_memory_resident_bytes=sample.xray.memory_resident_bytes if sample.xray is not None else None,
        xray_uptime_seconds=sample.xray.uptime_seconds if sample.xray is not None else None,
    )
