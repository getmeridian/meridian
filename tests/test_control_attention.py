"""Attention is a bounded, read-only derivation of aggregate user and node evidence."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from meridian.cli import app
from meridian.control import service as service_module
from meridian.control.client import ControlClient
from meridian.control.models import UserCreate
from meridian.control.service import ControlService
from meridian.core.agent_protocol import ObservedStateReport, StatsReport
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID, serve_control
from tests.test_control_service import _document, _report, _spec

NOW = "2026-11-01T12:00:00Z"


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ControlService]:
    monkeypatch.setattr(service_module, "utc_now", lambda: NOW)
    service = ControlService(
        tmp_path / "control.db",
        fleet_id=FLEET_ID,
        version="4.0.0",
        clock=lambda: datetime.fromisoformat(NOW).timestamp(),
    )
    yield service
    service.close()


def _person(
    service: ControlService,
    name: str,
    *,
    expires: str | None = None,
    limit: int = 0,
    used: int = 0,
    age: float = 0,
    carried: int = 0,
) -> None:
    user = service.create_user(UserCreate(username=name, expire_at=expires, traffic_limit_bytes=limit))
    created = (datetime.fromisoformat(NOW) - timedelta(days=age)).isoformat().replace("+00:00", "Z")
    service._db.execute(
        "UPDATE users SET created_at = ?, traffic_carried_bytes = ? WHERE username = ?", (created, carried, name)
    )
    if used:
        service.record_stats(
            "main",
            StatsReport.model_validate(
                {
                    "type": "stats",
                    "counter_epoch": "5b0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00",
                    "seq": service._db.total_changes,
                    "collected_at": NOW,
                    "users": [{"user_id": user.user_id, "uplink_bytes": used, "downlink_bytes": 0}],
                }
            ),
            now=NOW,
        )


@pytest.mark.parametrize(
    ("expires", "limit", "used", "age", "carried", "reasons"),
    [
        (None, 0, 0, 0, 0, []),
        ("2026-11-08T12:00:00Z", 0, 0, 0, 0, ["expiring"]),
        ("2026-11-08T12:00:00.5Z", 0, 0, 0, 0, []),
        (NOW, 0, 0, 0, 0, ["expiring"]),
        ("2026-11-01T11:59:59Z", 0, 0, 0, 0, []),
        (None, 100, 80, 0, 0, ["quota"]),
        (None, 100, 79, 0, 0, []),
        (None, 12345, 9876, 0, 0, ["quota"]),
        (None, 12345, 9875, 0, 0, []),
        (None, 0, 100, 0, 0, []),
        (None, 0, 0, 4, 0, ["unused"]),
        (None, 0, 0, 3, 0, []),
        (None, 0, 0, 4, 100, []),
        ("2026-11-05T12:00:00Z", 100, 80, 4, 0, ["expiring", "quota"]),
    ],
    ids=[
        "fresh",
        "expiry-at-seven-days",
        "expiry-fraction-outside",
        "expires-now",
        "past-expiry",
        "quota-at-eighty",
        "quota-below-eighty",
        "exact-quota-ratio",
        "below-exact-ratio",
        "unlimited",
        "unused-after-four-days",
        "three-days-is-not-more",
        "undated-imported-use",
        "two-reasons",
    ],
)
def test_attention_uses_inclusive_expiry_and_quota_but_strict_unused_age(
    service: ControlService, expires: str | None, limit: int, used: int, age: float, carried: int, reasons: list[str]
) -> None:
    _person(service, "alice", expires=expires, limit=limit, used=used, age=age, carried=carried)
    before = service._db.total_changes
    result = service.attention(now=NOW)
    assert service._db.total_changes == before
    assert [(person.username, person.reasons) for person in result.people] == ([("alice", reasons)] if reasons else [])
    assert result.observed_at == NOW and result.nodes == []


def test_a_reset_and_history_off_do_not_turn_previously_used_people_into_unused_invitations(
    service: ControlService,
) -> None:
    _person(service, "alice", used=80, age=10, limit=100)
    service.reset_user_traffic("alice")
    assert service.privacy().usage_history == "off"
    assert service.attention(now=NOW).people == []


def test_node_attention_uses_liveness_apply_failures_and_nonhealthy_reported_health(service: ControlService) -> None:
    for name in ("good", "offline", "failed", "unhealthy", "degraded", "unseen"):
        service.put_node_spec(name, _spec())
        if name != "unseen":
            service.node_seen(name, "0.1.0")
        if name in ("failed", "unhealthy", "degraded"):
            body = _report(_document(service, name), failed="core-unit" if name == "failed" else "")
            body["observed_at"] = NOW
            if name != "failed":
                body["health"]["status"] = name
            service.report(name, ObservedStateReport.model_validate(body), body)
    service._db.execute("UPDATE nodes SET last_seen_at = '2026-11-01T11:58:30Z' WHERE node_id = 'offline'")
    before = service._db.total_changes
    result = service.attention(now=NOW)
    assert service._db.total_changes == before
    assert {node.node_id: node.reasons for node in result.nodes} == {
        "offline": ["offline"],
        "failed": ["apply_failed"],
        "unhealthy": ["unhealthy"],
        "degraded": ["unhealthy"],
        "unseen": ["offline"],
    }
    assert "resources" not in json.dumps(result.to_data())


def test_attention_api_is_read_scoped_has_bounded_options_and_never_returns_credentials(
    service: ControlService, tmp_path: Path
) -> None:
    _person(service, "quota", limit=100, used=60)
    _person(service, "invite", age=4)
    _person(service, "expiry", expires="2026-11-04T12:00:00Z")
    secrets = [value for user in service.list_users() for value in (user.uuid, user.sub_token, user.user_id)]
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as admin:
        reader = admin.create_token("reader", "read")
        headers = {"Authorization": f"Bearer {reader.token}"}
        response = httpx.get(url + "/api/attention", headers=headers)
        assert response.status_code == 200
        assert {person["username"] for person in response.json()["people"]} == {"invite", "expiry"}
        custom = httpx.get(url + "/api/attention?expiry_days=1&quota_percent=60&unused_days=10", headers=headers)
        assert custom.status_code == 200
        assert custom.json()["people"] == [{"username": "quota", "reasons": ["quota"]}]
        assert not any(secret in response.text or secret in custom.text for secret in secrets)
        assert httpx.get(url + "/api/attention").status_code == 401
        for query in (
            "expiry_days=0",
            "expiry_days=31",
            "quota_percent=0",
            "quota_percent=101",
            "unused_days=0",
            "unused_days=366",
            "unused_days=bad",
            "quota_percent=80&quota_percent=90",
        ):
            response = httpx.get(url + "/api/attention?" + query, headers=headers)
            assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("json_mode", [False, True], ids=["human", "json"])
def test_client_list_attention_filters_console_people_and_adds_reasons(
    service: ControlService, tmp_path: Path, tmp_home: Path, monkeypatch: pytest.MonkeyPatch, json_mode: bool
) -> None:
    from meridian import i18n
    from meridian.cluster import ClusterConfig, PanelConfig
    from meridian.commands import client
    from meridian.console import set_json_mode
    from meridian.setup.single_server import single_server_intent

    _person(service, "alice", limit=100, used=80)
    _person(service, "console", age=4)
    _person(service, "fresh")
    monkeypatch.setattr(i18n, "_requested", None)
    monkeypatch.setattr(i18n, "_current", "en")
    with serve_control(tmp_path) as url:
        cluster = ClusterConfig(
            panel=PanelConfig(url=url, api_token=ADMIN_TOKEN),
            topology_intent=single_server_intent(None, server_ref="srv-a", client_name="alice"),
        )
        monkeypatch.setattr(client, "load_topology_cluster", lambda: cluster)
        try:
            args = ["--lang", "en", "client", "list", "--attention"] + (["--json"] if json_mode else [])
            result = CliRunner().invoke(app, args)
            assert result.exit_code == 0, result.output
            if json_mode:
                data = json.loads(result.stdout)["data"]
                assert data["summary"]["clients"] == 2
                people = {person["username"]: person for person in data["clients"]}
                assert people["alice"]["attention_reasons"] == ["quota"]
                assert people["console"]["attention_reasons"] == ["unused"]
                assert people["console"]["declared"] is False and "fresh" not in people
            else:
                assert "Reason" in result.output and "Near quota" in result.output and "Never used" in result.output
                assert "fresh" not in result.output
        finally:
            set_json_mode(False)


@pytest.mark.parametrize(
    ("health", "check", "age", "live", "reasons"),
    [
        ("healthy", "pass", 0, True, []),
        ("healthy", "fail", 0, True, ["unhealthy"]),
        ("healthy", "warn", 0, True, ["unhealthy"]),
        ("degraded", "warn", 0, True, ["unhealthy"]),
        ("unhealthy", "fail", 89, True, ["unhealthy"]),
        ("unhealthy", "fail", 90, True, []),
        ("unhealthy", "fail", 90, False, ["offline"]),
    ],
    ids=["healthy", "failing-check", "warning-check", "degraded", "fresh-report", "live-stale-report", "offline"],
)
def test_node_attention_uses_the_same_fresh_runtime_evidence_as_fleet_status(
    service: ControlService, health: str, check: str, age: int, live: bool, reasons: list[str]
) -> None:
    from meridian.control.models import NodeSpec
    from tests.support.node_runtime import node_report

    service.put_node_spec("main", NodeSpec(resources=[]))
    observed_at = (datetime.fromisoformat(NOW) - timedelta(seconds=age)).isoformat().replace("+00:00", "Z")
    raw = node_report(observed_at, health=health, check=check)
    service.report("main", ObservedStateReport.model_validate(raw), raw)
    if not live:
        service._db.execute("UPDATE nodes SET last_seen_at = ? WHERE node_id = 'main'", (observed_at,))
    before = service._db.total_changes
    attention = service.attention(now=NOW)
    assert service._db.total_changes == before
    assert {node.node_id: node.reasons for node in attention.nodes} == ({"main": reasons} if reasons else {})


def test_live_polls_do_not_make_a_stale_apply_failure_current_attention(service: ControlService) -> None:
    service.put_node_spec("main", _spec())
    raw = _report(_document(service), failed="core-unit")
    raw["observed_at"] = (datetime.fromisoformat(NOW) - timedelta(seconds=90)).isoformat().replace("+00:00", "Z")
    service.report("main", ObservedStateReport.model_validate(raw), raw)
    assert service.attention(now=NOW).nodes == []
    raw["observed_at"] = NOW
    service.report("main", ObservedStateReport.model_validate(raw), raw)
    assert {node.node_id: node.reasons for node in service.attention(now=NOW).nodes} == {"main": ["apply_failed"]}
