"""Integer means, peaks, and measured network intervals for hourly capacity buckets."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from meridian.control.times import format_timestamp
from meridian.core.capacity import CapacityHour, CapacityValues


@dataclass
class CapacityAccumulator:
    samples: int = 0
    sums: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    peaks: dict[str, int] = field(default_factory=dict)
    rx_bytes: int = 0
    tx_bytes: int = 0
    covered_millis: int = 0

    @classmethod
    def from_json(cls, text: str) -> CapacityAccumulator:
        data: dict[str, Any] = json.loads(text)
        return cls(**data)

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    def add(self, values: CapacityValues) -> None:
        self.samples += 1
        for name in type(values).model_fields:
            value = getattr(values, name)
            if value is not None:
                self.sums[name] = self.sums.get(name, 0) + value
                self.counts[name] = self.counts.get(name, 0) + 1
                self.peaks[name] = max(self.peaks.get(name, value), value)

    def combine(self, other: CapacityAccumulator) -> None:
        self.samples += other.samples
        self.rx_bytes += other.rx_bytes
        self.tx_bytes += other.tx_bytes
        self.covered_millis += other.covered_millis
        for name, value in other.sums.items():
            self.sums[name] = self.sums.get(name, 0) + value
            self.counts[name] = self.counts.get(name, 0) + other.counts[name]
            self.peaks[name] = max(self.peaks.get(name, other.peaks[name]), other.peaks[name])

    def mean(self) -> CapacityValues:
        return CapacityValues.model_validate({name: total // self.counts[name] for name, total in self.sums.items()})

    def peak(self) -> CapacityValues:
        return CapacityValues.model_validate(self.peaks)

    def hour(self, starts_at: int) -> CapacityHour:
        measured = self.covered_millis > 0
        return CapacityHour(
            starts_at=format_timestamp(starts_at),
            samples=self.samples,
            mean=self.mean(),
            peak=self.peak(),
            rx_bytes=self.rx_bytes if measured else None,
            tx_bytes=self.tx_bytes if measured else None,
            covered_seconds=min(self.covered_millis // 1000, 3600),
        )


def network_buckets(ends_at: float, interval_millis: int, rx: int, tx: int) -> list[tuple[int, int, int, int]]:
    """Split adjacent growth at UTC hour boundaries without losing a byte to rounding."""
    end = round(ends_at * 1000)
    start = end - interval_millis
    parts: list[tuple[int, int, int, int]] = []
    assigned_rx = assigned_tx = elapsed = 0
    while start < end:
        hour = start // 3_600_000 * 3600
        length = min(end, (hour + 3600) * 1000) - start
        elapsed += length
        cumulative_rx = rx * elapsed // interval_millis
        cumulative_tx = tx * elapsed // interval_millis
        parts.append((hour, length, cumulative_rx - assigned_rx, cumulative_tx - assigned_tx))
        assigned_rx, assigned_tx = cumulative_rx, cumulative_tx
        start += length
    return parts
