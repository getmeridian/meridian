"""Server capacity cards preserve missing evidence and render only local charts."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from meridian.control.models import NodeSpec
from meridian.control.service import ControlService
from meridian.control.ui_routes import UI_DIR
from meridian.core.agent_protocol import ObservedStateReport
from tests.support.capacity import NETWORK_EPOCH, NOW, capacity_report
from tests.support.console_dom import needs_console_dom, run_console
from tests.support.control_service import FLEET_ID
from tests.support.token_service import Clock

pytestmark = needs_console_dom


def server_data(tmp_path: Path, state: str) -> dict[str, object]:
    clock = Clock()
    clock.now = datetime.fromisoformat(NOW).timestamp()
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0", clock=clock)
    service.put_node_spec("main", NodeSpec(resources=[]))
    if state != "not_reported":
        service.node_seen("main", "0.2.17", capabilities=["capacity_telemetry"])
        raw = capacity_report(load1_milli=0, memory_available_bytes=0)
        service.report("main", ObservedStateReport.model_validate(raw), raw)
        clock.now += 60
        raw = capacity_report(
            sequence=2,
            load1_milli=0,
            memory_available_bytes=0,
            network={
                "counter_epoch": NETWORK_EPOCH,
                "rx_bytes": 0,
                "tx_bytes": 0,
                "rx_bytes_per_second": 0,
                "tx_bytes_per_second": 0,
                "interval_millis": 60_000,
            },
        )
        service.report("main", ObservedStateReport.model_validate(raw), raw)
        if state == "stale":
            clock.now += 181
    return {
        "servers": [
            {
                "node_id": "main",
                "role": "node",
                "live": False,
                "runtime_available": False,
                "traffic_bytes": 1024,
                "capacity": service.capacity.view("main").to_data(),
            }
        ]
    }


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_capacity_cards_show_cpu_memory_and_zero_host_rates(tmp_path: Path, locale: str) -> None:
    result = run_console("servers", locale, server_data(tmp_path, "current"))
    assert len(result["capacity_charts"]) == 3
    assert result["capacity_zeros"] >= 2
    assert all(request["url"] == "ui/api/fleet" for request in result["requests"])
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    for key in ("capacity.title", "capacity.rx_now", "capacity.tx_now", "capacity.host_traffic", "capacity.coverage"):
        assert catalog[key].split("{")[0] in result["text"]


@pytest.mark.parametrize("state", ["not_reported", "stale"])
def test_missing_and_stale_capacity_do_not_display_zero_now(tmp_path: Path, state: str) -> None:
    result = run_console("servers", "en", server_data(tmp_path, state))
    assert result["capacity_now"] == []
    assert state.replace("_", " ").lower() in result["text"].lower()


def test_capacity_charts_show_the_observed_peak_for_zero_load(tmp_path: Path) -> None:
    result = run_console("servers", "en", server_data(tmp_path, "current"))
    peak = result["capacity_peaks"][0].translate({ord(c): None for c in "\u2066\u2067\u2068\u2069"})
    assert peak == "Peak 0"
