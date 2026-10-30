"""A complete, secret-free node report for projection and console tests."""

from __future__ import annotations

from typing import Any


def node_report(observed_at: str, *, health: str = "healthy", check: str = "pass") -> dict[str, Any]:
    return {
        "type": "report",
        "fleet_id": "0f1e2d3c-4b5a-4968-8776-655443322110",
        "node_id": "main",
        "applied": {"fleet_epoch": 1, "revision": 1, "sha256": "a" * 64},
        "target": None,
        "resources": [],
        "health": {
            "status": health,
            "checks": [
                {"name": "xray.api", "status": "pass", "message": "core API ready"},
                {"name": "stats", "status": check, "message": "last collected 120 s ago"},
                {"name": "xray.uptime", "status": "pass", "message": "up 42s"},
            ],
            "uptime_seconds": 7200,
            "load1_milli": 1250,
            "memory_available_bytes": 2 * 1024**3,
            "disk_free_bytes": 5 * 1024**3,
        },
        "versions": {
            "agent": "0.1.0",
            "xray": "26.7.28",
            "realm": None,
            "nginx": "1.28.0",
            "os_id": "debian",
            "os_version": "13",
            "kernel": "6.1.0",
        },
        "observed_at": observed_at,
    }
