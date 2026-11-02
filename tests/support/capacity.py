"""Address-free capacity samples shared by protocol, history, and console scenarios."""

from typing import Any

from tests.support.node_runtime import node_report

COLLECTION_EPOCH = "80c5b659-5aad-425c-a786-512ad6a86ac1"
NETWORK_EPOCH = "de3e8b59-81cd-483d-a4b8-706fd151a024"
NOW = "2026-11-01T12:00:00Z"


def capacity_sample(*, sequence: int = 1, collected_at: str = NOW, **changes: Any) -> dict[str, Any]:
    return {
        "collection_epoch": COLLECTION_EPOCH,
        "sequence": sequence,
        "collected_at": collected_at,
        "cpu_count": 4,
        "load1_milli": 1250,
        "load5_milli": 750,
        "load15_milli": 500,
        "memory_total_bytes": 4 * 1024**3,
        "memory_available_bytes": 2 * 1024**3,
        "network": {
            "counter_epoch": NETWORK_EPOCH,
            "rx_bytes": 0,
            "tx_bytes": 0,
            "rx_bytes_per_second": None,
            "tx_bytes_per_second": None,
            "interval_millis": None,
        },
        "xray": {"uptime_seconds": 42, "memory_resident_bytes": 64 * 1024**2},
        "inbound_connections": None,
        **changes,
    }


def capacity_report(**changes: Any) -> dict[str, Any]:
    return {**node_report(NOW), "capacity": capacity_sample(**changes)}
