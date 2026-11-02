"""Negotiated, nullable capacity evidence extends the address-free report contract."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from meridian.control.times import format_timestamp
from meridian.core.agent_protocol import ObservedStateReport
from tests.support.agent_node import FakeNode
from tests.support.capacity import capacity_report
from tests.support.node_runtime import node_report
from tests.support.token_service import serve_tokens, token_request


def test_agent_report_accepts_capacity_without_addresses_or_floats() -> None:
    raw = capacity_report()
    report = ObservedStateReport.model_validate(raw)
    assert report.model_dump(mode="json")["capacity"] == raw["capacity"]
    text = report.model_dump_json()
    assert not any(word in text for word in ("client_ip", "device", "hostname", "interface"))
    json.loads(text, parse_float=lambda value: pytest.fail(f"A float entered the agent report: {value}"))


def test_old_reports_keep_capacity_absent() -> None:
    report = ObservedStateReport.model_validate(node_report("2026-11-01T12:00:00Z"))
    assert getattr(report, "capacity", None) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"cpu_count": 0},
        {"load1_milli": 1.25},
        {"memory_available_bytes": 8 * 1024**3},
        {"client_ip": "198.51.100.7"},
    ],
)
def test_invalid_capacity_is_refused(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ObservedStateReport.model_validate(capacity_report(**changes))


@pytest.mark.parametrize("negotiated", [False, True])
def test_capacity_reports_require_the_sessions_negotiated_capability(tmp_path: Path, negotiated: bool) -> None:
    with serve_tokens(tmp_path) as fleet:
        node = FakeNode()
        grant = fleet.service.grant_enrollment("main")
        status, enrolled = token_request(
            fleet.url + "/agent/v1/enroll", "POST", node.enroll_request(grant.enrollment_token)
        )
        assert status == 200
        node.certificate_pem = enrolled["certificate_pem"]
        status, welcome = token_request(
            fleet.url + "/agent/v1/hello",
            "POST",
            node.hello(format_timestamp(fleet.clock()), capabilities=["capacity_telemetry"] if negotiated else []),
        )
        assert status == 200
        assert ("capacity_telemetry" in welcome["capabilities"]) == negotiated
        status, answer = token_request(
            fleet.url + "/agent/v1/report", "POST", capacity_report(), welcome["session_token"]
        )
        assert status == (200 if negotiated else 400), answer
        assert (fleet.service.node_status("main").runtime is not None) == negotiated
