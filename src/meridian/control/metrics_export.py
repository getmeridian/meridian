"""Prometheus 0.0.4 text with node aggregates and fixed service-runtime labels only."""

from __future__ import annotations

from typing import get_args

from pydantic import ConfigDict, RootModel

from meridian.control.request_metrics import LATENCY_BUCKETS, RequestMetrics
from meridian.control.service import ControlService
from meridian.control.times import parse_timestamp
from meridian.core.capacity import CapacityStatus

METRICS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


class MetricsText(RootModel[str]):
    model_config = ConfigDict(frozen=True, strict=True)


class Exposition:
    def __init__(self) -> None:
        self._metadata: dict[str, tuple[str, str]] = {}
        self._samples: dict[str, list[str]] = {}

    def describe(self, name: str, kind: str, help_text: str) -> None:
        self._metadata[name] = (kind, help_text)

    def sample(self, family: str, value: int | float | str | None, *, suffix: str = "", **labels: str) -> None:
        if value is None:
            return
        pairs = ",".join(key + '="' + escape_label(value) + '"' for key, value in labels.items())
        label_text = "{" + pairs + "}" if pairs else ""
        self._samples.setdefault(family, []).append(f"{family}{suffix}{label_text} {value}")

    def gauge(self, name: str, value: int | float | str | None, help_text: str, **labels: str) -> None:
        self.describe(name, "gauge", help_text)
        self.sample(name, value, **labels)

    def counter(self, name: str, value: int | None, help_text: str, **labels: str) -> None:
        self.describe(name, "counter", help_text)
        self.sample(name, value, **labels)

    def text(self) -> MetricsText:
        lines: list[str] = []
        for name, samples in sorted(self._samples.items()):
            kind, help_text = self._metadata[name]
            lines.extend((f"# HELP {name} {help_text}", f"# TYPE {name} {kind}", *samples))
        return MetricsText("\n".join(lines) + "\n")


def escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def milli(value: int | None) -> str | None:
    return f"{value // 1000}.{value % 1000:03d}" if value is not None else None


def exposition(service: ControlService, requests: RequestMetrics) -> MetricsText:
    result = Exposition()
    result.gauge("meridian_control_uptime_seconds", f"{requests.uptime_seconds():.6f}", "Control HTTP runtime uptime.")
    result.gauge(
        "meridian_control_sqlite_bytes",
        service.sqlite_size_bytes(),
        "SQLite disk bytes including WAL and shared memory.",
    )
    for task in service.health().background_tasks:
        labels: dict[str, str] = {"task": task.name}
        result.gauge(
            "meridian_control_background_task_started",
            int(task.last_attempt_at is not None),
            "Task has attempted work.",
            **labels,
        )
        result.gauge(
            "meridian_control_background_task_error",
            int(task.error_code is not None),
            "Task retains a failed attempt.",
            **labels,
        )
        result.gauge(
            "meridian_control_background_task_healthy",
            int(task.last_success_at is not None and task.error_code is None),
            "Task has succeeded and has no current error.",
            **labels,
        )
        result.gauge(
            "meridian_control_background_task_last_attempt_timestamp_seconds",
            parse_timestamp(task.last_attempt_at) if task.last_attempt_at else None,
            "Latest task attempt time.",
            **labels,
        )
        result.gauge(
            "meridian_control_background_task_last_success_timestamp_seconds",
            parse_timestamp(task.last_success_at) if task.last_success_at else None,
            "Latest successful task time.",
            **labels,
        )
    for row in requests.snapshot():
        labels = {"route_id": row.route_id, "status_class": row.status_class}
        result.counter(
            "meridian_control_requests_total",
            row.count,
            "HTTP response attempts by fixed route ID and status class.",
            **labels,
        )
        name = "meridian_control_request_duration_seconds"
        result.describe(name, "histogram", "HTTP request duration by fixed route ID and status class.")
        for bound, count in zip(LATENCY_BUCKETS, row.buckets, strict=True):
            result.sample(name, count, suffix="_bucket", **labels, le=f"{bound:g}")
        result.sample(name, row.count, suffix="_bucket", **labels, le="+Inf")
        result.sample(name, row.count, suffix="_count", **labels)
        result.sample(name, f"{row.seconds:.9f}", suffix="_sum", **labels)
    for node in service.list_nodes():
        labels = {"node": node.node_id}
        current = node.capacity
        for status in get_args(CapacityStatus):
            result.gauge(
                "meridian_node_capacity_status",
                int(current.status == status),
                "One state is active: current, stale, unavailable, or not reported.",
                **labels,
                state=status,
            )
        result.gauge(
            "meridian_node_capacity_report_age_seconds",
            current.age_seconds,
            "Age of the last accepted capacity collection at the service.",
            **labels,
        )
        counted = node.traffic_counting_since is not None or node.traffic_bytes > 0
        result.gauge(
            "meridian_node_counted_traffic_available",
            int(counted),
            "Node has accepted people-traffic accounting evidence.",
            **labels,
        )
        result.counter(
            "meridian_node_counted_traffic_bytes_total",
            node.traffic_bytes if counted else None,
            "Durable counted people traffic in both directions; excludes host overhead.",
            **labels,
        )
        result.gauge(
            "meridian_node_counted_traffic_last_received_timestamp_seconds",
            parse_timestamp(node.active_report_received_at) if node.active_report_received_at else None,
            "Latest accepted people-traffic report at the service.",
            **labels,
        )
        if current.status != "current" or current.sample is None or current.values is None:
            continue
        sample, values = current.sample, current.values
        result.gauge("meridian_node_cpu_count", sample.cpu_count, "Host logical CPUs.", **labels)
        for minutes, value in ((1, sample.load1_milli), (5, sample.load5_milli), (15, sample.load15_milli)):
            result.gauge(
                f"meridian_node_cpu_load{minutes}", milli(value), f"Host {minutes}-minute load average.", **labels
            )
        result.gauge(
            "meridian_node_cpu_load_per_cpu",
            milli(values.load_per_cpu_milli),
            "One-minute host load per logical CPU.",
            **labels,
        )
        result.gauge("meridian_node_memory_total_bytes", sample.memory_total_bytes, "Host total memory.", **labels)
        result.gauge(
            "meridian_node_memory_available_bytes", sample.memory_available_bytes, "Host available memory.", **labels
        )
        result.gauge(
            "meridian_node_memory_used_ratio",
            milli(values.memory_used_per_mille),
            "Host used memory divided by total memory.",
            **labels,
        )
        if sample.network is not None:
            network = sample.network
            result.counter(
                "meridian_node_host_receive_bytes_total",
                network.rx_bytes,
                "Default-route host receive bytes since counter epoch; includes overhead and other services.",
                **labels,
            )
            result.counter(
                "meridian_node_host_send_bytes_total",
                network.tx_bytes,
                "Default-route host send bytes since counter epoch; includes overhead and other services.",
                **labels,
            )
            result.gauge(
                "meridian_node_host_receive_bytes_per_second",
                network.rx_bytes_per_second,
                "Host receive rate over adjacent monotonic collections.",
                **labels,
            )
            result.gauge(
                "meridian_node_host_send_bytes_per_second",
                network.tx_bytes_per_second,
                "Host send rate over adjacent monotonic collections.",
                **labels,
            )
        if sample.xray is not None:
            result.gauge(
                "meridian_node_xray_uptime_seconds",
                sample.xray.uptime_seconds,
                "Managed Xray process uptime.",
                **labels,
            )
            result.gauge(
                "meridian_node_xray_memory_resident_bytes",
                sample.xray.memory_resident_bytes,
                "Managed Xray resident memory.",
                **labels,
            )
    return result.text()
