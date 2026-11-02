"""Bounded in-memory HTTP counters and latency buckets, keyed only by fixed route IDs."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from meridian.control.route_types import ApiRoute

LATENCY_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 120.0)


def request_route_id(handler: Callable[..., Any], route: ApiRoute | None) -> str:
    """Catalog operation IDs win; internal handlers use a fixed module-qualified name."""
    return route.operation_id if route is not None else handler.__module__.rsplit(".", 1)[-1] + "." + handler.__name__


@dataclass
class RequestSeries:
    count: int = 0
    seconds: float = 0.0
    buckets: list[int] = field(default_factory=lambda: [0] * len(LATENCY_BUCKETS))


@dataclass(frozen=True)
class RequestSnapshot:
    route_id: str
    status_class: str
    count: int
    seconds: float
    buckets: tuple[int, ...]


class RequestMetrics:
    def __init__(self, route_ids: Iterable[str], *, clock: Callable[[], float] = time.monotonic) -> None:
        self._route_ids = frozenset(route_ids) | {"unmatched"}
        self._clock = clock
        self._started = clock()
        self._lock = threading.Lock()
        self._series: dict[tuple[str, str], RequestSeries] = {}

    def observe(self, route_id: str, status: int, seconds: float) -> None:
        if not math.isfinite(seconds):
            return
        seconds = max(0.0, seconds)
        route_id = route_id if route_id in self._route_ids else "unmatched"
        status_class = f"{status // 100}xx" if 100 <= status < 600 else "other"
        with self._lock:
            series = self._series.setdefault((route_id, status_class), RequestSeries())
            series.count += 1
            series.seconds += seconds
            for index, bound in enumerate(LATENCY_BUCKETS):
                if seconds <= bound:
                    series.buckets[index] += 1

    def uptime_seconds(self) -> float:
        return max(0.0, self._clock() - self._started)

    def snapshot(self) -> list[RequestSnapshot]:
        with self._lock:
            return [
                RequestSnapshot(route, status, series.count, series.seconds, tuple(series.buckets))
                for (route, status), series in sorted(self._series.items())
            ]
