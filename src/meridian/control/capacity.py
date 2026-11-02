"""Bounded node-only capacity evidence; no device or per-person records."""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable

from pydantic import ValidationError

from meridian.control.capacity_rollups import CapacityAccumulator, network_buckets
from meridian.control.errors import ServiceError
from meridian.control.store import Store
from meridian.control.times import format_timestamp, parse_timestamp
from meridian.core.agent_capacity import NodeCapacity
from meridian.core.capacity import (
    CAPACITY_RECENT_INTERVAL_SECONDS,
    CAPACITY_RECENT_POINTS,
    CAPACITY_RECENT_SECONDS,
    CAPACITY_RETENTION_HOURS,
    CAPACITY_STALE_SECONDS,
    CapacityCurrent,
    CapacityDay,
    CapacityPoint,
    CapacityQuery,
    CapacityStatus,
    NodeCapacityView,
    capacity_values,
)

CAPACITY_TABLES = (
    """CREATE TABLE IF NOT EXISTS node_capacity_state (
        node_id TEXT PRIMARY KEY REFERENCES nodes(node_id) ON DELETE CASCADE,
        fleet_epoch INTEGER NOT NULL,
        sample TEXT NOT NULL,
        received_at REAL NOT NULL,
        retired_epochs TEXT NOT NULL DEFAULT '[]'
    )""",
    """CREATE TABLE IF NOT EXISTS node_capacity_hourly (
        node_id TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
        hour_start INTEGER NOT NULL,
        data TEXT NOT NULL,
        PRIMARY KEY (node_id, hour_start)
    )""",
    "CREATE INDEX IF NOT EXISTS node_capacity_retention ON node_capacity_hourly(hour_start)",
)
MAX_RETIRED_COLLECTION_EPOCHS = 8


class CapacityLedger:
    def __init__(self, store: Store, *, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock
        self._recent: dict[str, deque[CapacityPoint]] = {}

    def record(self, node_id: str, sample: NodeCapacity | None) -> None:
        """Called inside the report transaction; replays never renew receipt time or add history."""
        if sample is None or self._store.row("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)) is None:
            return
        now = self._clock()
        row = self._store.row("SELECT * FROM node_capacity_state WHERE node_id=?", (node_id,))
        previous = NodeCapacity.model_validate_json(row["sample"]) if row else None
        retired: list[str] = json.loads(row["retired_epochs"]) if row else []
        if sample.collection_epoch in retired:
            return
        if previous is not None:
            if sample.collection_epoch == previous.collection_epoch:
                if sample.sequence <= previous.sequence:
                    return
            else:
                retired = [*retired, previous.collection_epoch][-MAX_RETIRED_COLLECTION_EPOCHS:]
        values = capacity_values(sample)
        hour = int(now // 3600) * 3600
        buckets: dict[int, CapacityAccumulator] = {hour: self._bucket(node_id, hour)}
        buckets[hour].add(values)
        if previous is not None and previous.network is not None and sample.network is not None:
            old, new = previous.network, sample.network
            same_epoch = old.counter_epoch == new.counter_epoch
            if same_epoch and (new.rx_bytes < old.rx_bytes or new.tx_bytes < old.tx_bytes):
                raise ServiceError("counter_regression", "Host counters went down without a new counter epoch.", 400)
            if (
                same_epoch
                and sample.collection_epoch == previous.collection_epoch
                and sample.sequence == previous.sequence + 1
                and new.interval_millis is not None
            ):
                for start, millis, rx, tx in network_buckets(
                    now, new.interval_millis, new.rx_bytes - old.rx_bytes, new.tx_bytes - old.tx_bytes
                ):
                    if start not in buckets:
                        buckets[start] = self._bucket(node_id, start)
                    bucket = buckets[start]
                    bucket.covered_millis += millis
                    bucket.rx_bytes += rx
                    bucket.tx_bytes += tx
        for start, bucket in buckets.items():
            self._store.db.execute(
                "INSERT INTO node_capacity_hourly VALUES (?, ?, ?)"
                " ON CONFLICT(node_id,hour_start) DO UPDATE SET data=excluded.data",
                (node_id, start, bucket.to_json()),
            )
        self._store.db.execute(
            "INSERT INTO node_capacity_state VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(node_id) DO UPDATE SET fleet_epoch=excluded.fleet_epoch, sample=excluded.sample,"
            " received_at=excluded.received_at, retired_epochs=excluded.retired_epochs",
            (node_id, self._store.fleet_epoch, sample.model_dump_json(), now, json.dumps(retired)),
        )
        # One point per ten-second bucket; SQLite retains hourly means and peaks.
        points = self._recent.setdefault(node_id, deque(maxlen=CAPACITY_RECENT_POINTS))
        point = CapacityPoint(received_at=format_timestamp(now), values=values)
        if points and int(parse_timestamp(points[-1].received_at) // CAPACITY_RECENT_INTERVAL_SECONDS) == int(
            now // CAPACITY_RECENT_INTERVAL_SECONDS
        ):
            points[-1] = point
        else:
            points.append(point)
        self.prune(now=now)

    def current(self, node_id: str, *, now: float | None = None) -> CapacityCurrent:
        moment = self._clock() if now is None else now
        with self._store.lock:
            node = self._store.row("SELECT agent_capabilities FROM nodes WHERE node_id=?", (node_id,))
            capabilities = json.loads(node["agent_capabilities"] or "[]") if node else []
            if "capacity_telemetry" not in capabilities:
                return CapacityCurrent()
            row = self._store.row("SELECT * FROM node_capacity_state WHERE node_id=?", (node_id,))
            if row is None:
                return CapacityCurrent(status="unavailable")
            try:
                sample = NodeCapacity.model_validate_json(row["sample"])
            except ValidationError:
                return CapacityCurrent(status="unavailable")
            age = max(0, int(moment - row["received_at"]))
            available = any(value is not None for value in capacity_values(sample).model_dump().values())
            status: CapacityStatus = (
                "stale"
                if age >= CAPACITY_STALE_SECONDS or row["fleet_epoch"] != self._store.fleet_epoch
                else "current"
                if available
                else "unavailable"
            )
            return CapacityCurrent(
                status=status,
                sample=sample,
                values=capacity_values(sample),
                received_at=format_timestamp(row["received_at"]),
                age_seconds=age,
            )

    def view(self, node_id: str, *, hours: int = 24, now: float | None = None) -> NodeCapacityView:
        hours = CapacityQuery(hours=hours).hours
        moment = self._clock() if now is None else now
        hour = int(moment // 3600) * 3600
        day_start = hour - 23 * 3600
        with self._store.lock:
            if self._store.row("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)) is None:
                raise ServiceError("not_found", "This node is not registered.", 404)
            rows = self._store.rows(
                "SELECT hour_start,data FROM node_capacity_hourly WHERE node_id=? AND hour_start>=?"
                " AND hour_start<=? ORDER BY hour_start",
                (node_id, hour - (max(hours, 24) - 1) * 3600, hour),
            )
            accumulators = [(int(row["hour_start"]), CapacityAccumulator.from_json(row["data"])) for row in rows]
            day = CapacityAccumulator()
            for start, bucket in accumulators:
                if start >= day_start:
                    day.combine(bucket)
            recent = [
                point
                for point in self._recent.get(node_id, ())
                if moment - CAPACITY_RECENT_SECONDS < parse_timestamp(point.received_at) <= moment
            ]
            return NodeCapacityView(
                **self.current(node_id, now=moment).model_dump(),
                node_id=node_id,
                recent=recent,
                hourly=[bucket.hour(start) for start, bucket in accumulators if start >= hour - (hours - 1) * 3600],
                last_day=CapacityDay(
                    starts_at=format_timestamp(day_start),
                    ends_at=format_timestamp(moment),
                    samples=day.samples,
                    mean=day.mean(),
                    peak=day.peak(),
                    rx_bytes=day.rx_bytes if day.covered_millis else None,
                    tx_bytes=day.tx_bytes if day.covered_millis else None,
                    covered_seconds=min(day.covered_millis // 1000, 86_400),
                ),
            )

    def prune(self, *, now: float | None = None) -> None:
        moment = self._clock() if now is None else now
        cutoff = int(moment // 3600) * 3600 - (CAPACITY_RETENTION_HOURS - 1) * 3600
        with self._store.lock:
            self._store.db.execute("DELETE FROM node_capacity_hourly WHERE hour_start<?", (cutoff,))
            nodes = {str(row["node_id"]) for row in self._store.rows("SELECT node_id FROM nodes")}
            for node_id in list(self._recent):
                if node_id not in nodes:
                    del self._recent[node_id]
                    continue
                points = self._recent[node_id]
                while points and parse_timestamp(points[0].received_at) <= moment - CAPACITY_RECENT_SECONDS:
                    points.popleft()

    def forget(self, node_id: str) -> None:
        with self._store.lock:
            self._recent.pop(node_id, None)

    def _bucket(self, node_id: str, hour: int) -> CapacityAccumulator:
        row = self._store.row("SELECT data FROM node_capacity_hourly WHERE node_id=? AND hour_start=?", (node_id, hour))
        return CapacityAccumulator.from_json(row["data"]) if row else CapacityAccumulator()
