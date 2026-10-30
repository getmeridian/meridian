"""Billing periods use accepted node totals without retaining client or device data."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from meridian.control.client import ControlClient
from meridian.control.models import NodeSpec, ServiceSettings
from meridian.control.service import ControlService
from meridian.core.agent_protocol import StatsReport
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID, serve_control

NOW = "2026-11-14T12:00:00Z"
EPOCH = "5b0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00"


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ControlService]:
    for module in ("service", "service_settings", "server_allowances", "traffic"):
        if module == "server_allowances":
            continue
        monkeypatch.setattr(f"meridian.control.{module}.utc_now", lambda: NOW, raising=False)
    value = ControlService(
        tmp_path / "control.db",
        fleet_id=FLEET_ID,
        version="4.0.0",
        clock=lambda: datetime.fromisoformat(NOW).timestamp(),
    )
    value.put_node_spec("main", NodeSpec(resources=[]))
    value.put_user("sample")
    yield value
    value.close()


def configure(service: ControlService, *, reset_day: int = 15, **metadata: object) -> None:
    service.put_settings(
        ServiceSettings.model_validate(
            {
                "profile_title": "Meridian",
                "servers": {
                    "srv-a": {
                        "node_id": "main",
                        "metadata": {
                            "allowance_bytes": 100,
                            "reset_day": reset_day,
                            **metadata,
                        },
                    }
                },
            }
        )
    )


def count(service: ControlService, total: int, *, seq: int = 1, now: str = NOW, epoch: str = EPOCH) -> None:
    user = service.get_user("sample")
    assert user is not None
    report = StatsReport.model_validate(
        {
            "type": "stats",
            "counter_epoch": epoch,
            "seq": seq,
            "collected_at": now,
            "users": [{"user_id": user.user_id, "uplink_bytes": total // 2, "downlink_bytes": total - total // 2}],
        }
    )
    service.record_stats("main", report, now=now)


def test_allowance_counts_both_directions_without_replays_or_new_node_documents(service: ControlService) -> None:
    before = service.node_status("main")
    configure(service, provider="Example provider", pay_on="2026-11-15", pay_every="month")
    count(service, 80)
    count(service, 80)
    (usage,) = service.server_usage(now=NOW).servers
    assert usage.used_bytes == 80 and usage.lifetime_bytes == 80
    assert usage.measurement == "accepted_user_traffic"
    assert "Approximate" in usage.coverage and "overhead" in usage.coverage and "forwarding" in usage.coverage
    assert usage.period_start == "2026-10-15T00:00:00Z" and usage.period_end == "2026-11-15T00:00:00Z"
    assert usage.counting_since == NOW
    after = service.node_status("main")
    assert (before.revision, before.sha256) == (after.revision, after.sha256)
    assert service.privacy().usage_history == "off" and service.privacy().device_limits == "off"


def test_midnight_stats_advance_offsets_before_counting_and_survive_restart(
    service: ControlService, tmp_path: Path
) -> None:
    configure(service)
    count(service, 80)
    boundary = "2026-11-15T00:00:00Z"
    count(service, 110, seq=2, now=boundary)
    service.enforce(boundary)
    (usage,) = service.server_usage(now=boundary).servers
    assert usage.used_bytes == 30 and usage.lifetime_bytes == 110
    assert usage.period_start == boundary and usage.period_end == "2026-12-15T00:00:00Z"
    restarted = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert restarted.server_usage(now=boundary).servers[0].used_bytes == 30
        assert restarted.settings().servers == service.settings().servers
    finally:
        restarted.close()


def test_tracking_starts_at_reviewed_apply_and_label_or_allowance_edits_keep_use(service: ControlService) -> None:
    count(service, 50)
    configure(service)
    count(service, 80, seq=2)
    configure(service, provider="Updated provider", allowance_bytes=200, pay_on="2026-11-20")
    assert service.server_usage(now=NOW).servers[0].used_bytes == 30
    configure(service, reset_day=20)
    assert service.server_usage(now=NOW).servers[0].used_bytes == 0


def test_retired_epochs_and_person_removal_do_not_reduce_allowance_use(service: ControlService) -> None:
    configure(service)
    count(service, 70)
    count(service, 20, epoch="6b0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00")
    count(service, 80, seq=2)
    service.reset_user_traffic("sample")
    service.delete_user("sample")
    assert service.server_usage(now=NOW).servers[0].used_bytes == 100


def test_reading_a_new_period_and_repeating_dates_never_writes(service: ControlService) -> None:
    configure(service, pay_on="2026-10-31", pay_every="month")
    count(service, 80)
    before = service._db.total_changes
    future = "2027-02-28T12:00:00Z"
    (usage,) = service.server_usage(now=future).servers
    assert usage.used_bytes == 0 and str(usage.next_payment_on) == "2027-02-28"
    assert service.attention(now=future).servers[0].reasons == ["payment_due"]
    assert service._db.total_changes == before
    assert str(service.settings().servers["srv-a"].metadata.pay_on) == "2026-10-31"


@pytest.mark.parametrize(
    ("used", "pay_on", "repeat", "reasons"),
    [
        (79, None, None, []),
        (80, None, None, ["allowance_near"]),
        (100, None, None, ["allowance_exceeded"]),
        (130, "2026-11-21", None, ["allowance_exceeded", "payment_due"]),
        (0, "2026-11-22", None, []),
        (0, "2026-11-14", None, ["payment_due"]),
        (0, "2026-11-13", None, ["payment_overdue"]),
        (0, "2026-11-13", "month", []),
    ],
)
def test_server_attention_has_inclusive_thresholds_and_one_off_overdue_dates(
    service: ControlService, used: int, pay_on: str | None, repeat: str | None, reasons: list[str]
) -> None:
    configure(service, pay_on=pay_on, pay_every=repeat)
    count(service, used)
    before = service._db.total_changes
    result = service.attention(now=NOW)
    assert [(row.server_ref, row.reasons) for row in result.servers] == ([("srv-a", reasons)] if reasons else [])
    assert service._db.total_changes == before


def test_relay_and_control_without_an_agent_are_not_measured(service: ControlService) -> None:
    service.put_settings(
        ServiceSettings.model_validate(
            {
                "profile_title": "Meridian",
                "servers": {
                    ref: {
                        "node_id": None,
                        "metadata": {
                            "allowance_bytes": 100,
                            "pay_on": "2026-11-13",
                        },
                    }
                    for ref in ("srv-relay", "srv-control")
                },
            }
        )
    )
    usage = service.server_usage(now=NOW)
    assert all(
        row.measurement == "not_measured" and row.used_bytes is None and row.lifetime_bytes is None
        for row in usage.servers
    )
    assert all("Not measured" in row.coverage for row in usage.servers)
    assert {row.server_ref: row.reasons for row in service.attention(now=NOW).servers} == {
        "srv-control": ["payment_overdue"],
        "srv-relay": ["payment_overdue"],
    }


def test_server_evidence_reaches_read_scoped_api_and_console_with_bounded_attention_options(
    service: ControlService, tmp_path: Path
) -> None:
    configure(service)
    count(service, 80)
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as admin:
        reader = admin.create_token("reader", "read")
        headers = {"Authorization": f"Bearer {reader.token}"}
        response = httpx.get(url + "/api/servers", headers=headers)
        assert response.status_code == 200
        assert response.json()["servers"][0]["used_bytes"] == 80
        assert "Approximate" in response.json()["servers"][0]["coverage"]
        assert httpx.get(url + "/api/servers").status_code == 401
        assert admin.server_usage().servers[0].metadata.allowance_bytes == 100
        attention = httpx.get(url + "/api/attention", headers=headers)
        assert attention.json()["servers"][0]["reasons"] == ["allowance_near"]
        assert httpx.get(url + "/api/attention?allowance_percent=90", headers=headers).json()["servers"] == []
        for query in (
            "allowance_percent=0",
            "allowance_percent=101",
            "allowance_exceeded_percent=0",
            "allowance_exceeded_percent=101",
            "allowance_percent=90&allowance_exceeded_percent=80",
            "payment_days=0",
            "payment_days=31",
            "payment_days=bad",
            "payment_days=1&payment_days=2",
        ):
            result = httpx.get(url + "/api/attention?" + query, headers=headers)
            assert result.status_code == 400 and result.json()["error"]["code"] == "invalid_request"
        console = httpx.get(url + "/ui/api/fleet", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
        assert console.status_code == 200
        assert console.json()["server_usage"][0]["used_bytes"] == 80
        assert console.json()["server_attention"][0]["reasons"] == ["allowance_near"]
        user = service.get_user("sample")
        assert user is not None
        assert all(
            secret not in response.text + attention.text + console.text for secret in (user.uuid, user.sub_token)
        )


def test_billing_attention_does_not_change_runtime_health(service: ControlService) -> None:
    from meridian.core.fleet import FleetSources, FleetTopology, TopologyPanel, build_fleet_status

    configure(service, pay_on="2026-11-13")
    topology = FleetTopology(
        panel=TopologyPanel(
            url="https://198.51.100.10",
            display_url="https://198.51.100.10",
            server_ip="198.51.100.10",
            ssh_user="root",
            ssh_port=22,
            deployed_with="4.0.0",
        )
    )
    status = build_fleet_status(
        topology,
        panel_healthy=True,
        sources=FleetSources(panel="available", nodes="available", users="available"),
        server_usage=service.server_usage(now=NOW),
    )
    assert status.summary.health == "healthy" and status.summary.needs_attention
    assert status.server_attention[0].reasons == ["payment_overdue"]
    assert status.server_usage[0].metadata.pay_on is not None
