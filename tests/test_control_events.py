"""Secret-free lifecycle events, atomic writes, debounced node transitions, cursors, and retention."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from meridian.cli import app
from meridian.control import service as service_module
from meridian.control.client import ControlClient
from meridian.control.models import DeviceKeyCreate, PrivacySettings, UserCreate, UserUpdate
from meridian.control.restore import stage_restored_database
from meridian.control.service import ControlService
from meridian.control.snapshot import decrypt_snapshot, generate_key_pair
from meridian.control.times import format_timestamp
from meridian.core.agent_protocol import ObservedStateReport, StatsReport
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID, serve_control
from tests.test_control_service import _document, _report, _spec

NOW = "2026-11-01T12:00:00Z"


@pytest.fixture
def clock() -> list[float]:
    return [datetime.fromisoformat(NOW).timestamp()]


@pytest.fixture
def service(tmp_path: Path, clock: list[float], monkeypatch: pytest.MonkeyPatch) -> Iterator[ControlService]:
    monkeypatch.setattr(service_module, "utc_now", lambda: format_timestamp(clock[0]))
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0", clock=lambda: clock[0])
    yield service
    service.close()


def test_user_lifecycle_events_name_fields_and_never_hold_credentials(service: ControlService) -> None:
    before = service.put_user("alice")
    assert service.put_user("alice") == before
    service.update_user("alice", UserUpdate(traffic_limit_bytes=100, expire_at="2026-11-10T00:00:00Z", device_limit=4))
    service.extend_user("alice", days=7)
    service.update_user("alice", UserUpdate(status="disabled"))
    service.update_user("alice", UserUpdate(status="disabled"))
    service.update_user("alice", UserUpdate(status="active"))
    reissued = service.reissue_user("alice")
    service.reset_user_traffic("alice")
    service.delete_user("alice")
    service.delete_user("alice")

    page = service.events.list(limit=2000)
    assert [event.kind for event in page.events] == [
        "user.created",
        "user.updated",
        "user.updated",
        "user.disabled",
        "user.enabled",
        "user.reissued",
        "user.traffic_reset",
        "user.removed",
    ]
    assert all((event.actor, event.subject, event.time) == ("root", "alice", NOW) for event in page.events)
    assert page.events[1].to_data()["detail"] == {"fields": ["device_limit", "expire_at", "traffic_limit_bytes"]}
    assert page.events[2].to_data()["detail"] == {"fields": ["expire_at"]}
    text = json.dumps(page.to_data())
    for secret in (before.user_id, before.uuid, before.sub_token, reissued.uuid, reissued.sub_token):
        assert secret not in text
    assert set(page.events[0].to_data()) == {"id", "time", "kind", "actor", "subject", "detail"}


@pytest.mark.parametrize("action", ["create", "update", "extend", "disable", "reissue", "reset", "remove"])
def test_an_event_failure_rolls_back_the_user_mutation(service: ControlService, action: str) -> None:
    user = service.put_user("alice")
    before = service.events.list(limit=2000)
    service._store.create(
        "CREATE TRIGGER refuse_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'event refused'); END;"
    )
    with pytest.raises(sqlite3.IntegrityError, match="event refused"):
        if action == "create":
            service.create_user(UserCreate(username="bob"))
        elif action == "update":
            service.update_user("alice", UserUpdate(traffic_limit_bytes=10))
        elif action == "extend":
            service.extend_user("alice", days=7)
        elif action == "disable":
            service.update_user("alice", UserUpdate(status="disabled"))
        elif action == "reissue":
            service.reissue_user("alice")
        elif action == "reset":
            service.reset_user_traffic("alice")
        else:
            service.delete_user("alice")
    assert service.get_user("alice") == user
    assert service.get_user("bob") is None
    assert service.events.list(limit=2000) == before


@pytest.mark.parametrize("action", ["create", "extend"])
def test_composition_failure_rolls_back_an_already_inserted_event(
    service: ControlService, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    user = service.put_user("alice") if action == "extend" else None
    before = service.events.list()

    def refuse() -> None:
        raise RuntimeError("composition refused")

    monkeypatch.setattr(service, "_compose_all", refuse)
    with pytest.raises(RuntimeError, match="composition refused"):
        if action == "extend":
            service.extend_user("alice", days=7)
        else:
            service.put_user("alice")
    assert service.get_user("alice") == user
    assert service.events.list() == before


def test_a_refused_foreign_fleet_cannot_prune_local_event_history(
    service: ControlService, tmp_path: Path, clock: list[float]
) -> None:
    from meridian.control.errors import ServiceError

    service.put_user("alice")
    before = service.events.list()
    with pytest.raises(ServiceError, match="another fleet"):
        ControlService(
            tmp_path / "control.db",
            fleet_id="0f1e2d3c-4b5a-4968-8776-655443322111",
            version="4.0.0",
            clock=lambda: clock[0] + 181 * 86400,
        )
    assert service.events.list() == before


def test_limit_expiry_and_restoration_events_are_system_transitions_and_replays_add_nothing(
    service: ControlService, clock: list[float]
) -> None:
    user = service.create_user(UserCreate(username="alice", traffic_limit_bytes=10, expire_at="2026-11-01T12:01:00Z"))
    report = StatsReport.model_validate(
        {
            "type": "stats",
            "counter_epoch": "5b0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00",
            "seq": 1,
            "collected_at": NOW,
            "users": [{"user_id": user.user_id, "uplink_bytes": 4, "downlink_bytes": 6}],
        }
    )
    service.record_stats("main", report)
    count = len(service.events.list().events)
    service.record_stats("main", report)
    service.enforce()
    assert len(service.events.list().events) == count
    service.reset_user_traffic("alice")
    clock[0] += 60
    service.enforce()
    service.enforce()
    service.update_user("alice", UserUpdate(expire_at=None))
    transitions = [
        event
        for event in service.events.list().events
        if event.kind in {"user.limited", "user.expired", "user.restored"}
    ]
    assert [(event.kind, event.actor) for event in transitions] == [
        ("user.limited", "system"),
        ("user.restored", "system"),
        ("user.expired", "system"),
        ("user.restored", "system"),
    ]


def test_nodes_go_offline_only_after_the_live_window_and_recover_once(
    service: ControlService, clock: list[float]
) -> None:
    service.put_node_spec("main", _spec())
    service.enforce()
    assert service.events.list().events == []
    clock[0] += 89
    service.enforce()
    assert service.events.list().events == []
    clock[0] += 1
    service.enforce()
    service.enforce()
    assert [event.kind for event in service.events.list().events] == ["node.offline"]
    service.node_seen("main", "0.1.0")
    service.enforce()
    service.enforce()
    assert [event.kind for event in service.events.list().events] == ["node.offline", "node.online"]
    clock[0] += 89
    service.enforce()
    assert len(service.events.list().events) == 2
    clock[0] += 1
    service.enforce()
    service.enforce()
    assert [event.kind for event in service.events.list().events] == ["node.offline", "node.online", "node.offline"]
    assert all((event.actor, event.subject) == ("system", "main") for event in service.events.list().events)


def test_apply_failures_are_debounced_and_never_copy_raw_errors(service: ControlService) -> None:
    service.put_node_spec("main", _spec())
    document = _document(service)
    failed = _report(document, failed="core-unit")
    failed["resources"][-1]["error"]["message"] = "credential 5b0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00 at 198.51.100.7"
    service.report("main", ObservedStateReport.model_validate(failed), failed)
    service.enforce()
    service.enforce()
    good = _report(document)
    service.report("main", ObservedStateReport.model_validate(good), good)
    service.enforce()
    service.enforce()
    events = [event for event in service.events.list().events if event.kind.startswith("node.apply")]
    assert [event.kind for event in events] == ["node.apply_failed", "node.apply_recovered"]
    text = json.dumps([event.to_data() for event in events])
    assert "198.51.100.7" not in text and "5b0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00" not in text


def test_events_keep_at_most_two_thousand_and_expire_after_one_hundred_eighty_days(
    service: ControlService, clock: list[float]
) -> None:
    for _ in range(2005):
        service.put_user("alice") if _ == 0 else service.events.record("user.created", "alice")
    page = service.events.list(limit=2000)
    assert len(page.events) == 2000 and page.events[0].id == 6 and page.events[-1].id == 2005
    clock[0] += 180 * 86400
    service.enforce()
    assert len(service.events.list(limit=2000).events) == 2000
    clock[0] += 1
    service.enforce()
    assert service.events.list().events == []
    service.put_user("bob")
    assert service.events.list().events[0].id == 2006


def test_privacy_events_point_at_the_audit_and_rollback_with_it(service: ControlService) -> None:
    settings = PrivacySettings(usage_history="daily")
    service.set_privacy(settings, author="owner")
    service.set_privacy(settings, author="owner")
    (event,) = service.events.list().events
    assert (event.kind, event.actor, event.subject) == ("privacy.changed", "owner", "privacy")
    audit = service._store.row("SELECT id FROM privacy_changes")
    assert event.to_data()["detail"] == {"audit_id": audit["id"]}
    assert service.privacy_state().changes[0].id == audit["id"]
    service._store.create(
        "CREATE TRIGGER refuse_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'event refused'); END;"
    )
    with pytest.raises(sqlite3.IntegrityError, match="event refused"):
        service.set_privacy(PrivacySettings(), author="root")
    assert service.privacy() == settings and len(service.privacy_state().changes) == 1


def test_event_api_is_read_scoped_and_paginates_with_secret_free_token_events(tmp_path: Path) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as admin:
        root_headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}
        admin.put_user("alice")
        read = admin.create_token("reader", "read")
        headers = {"Authorization": f"Bearer {read.token}"}
        first = httpx.get(url + "/api/events?limit=1", headers=headers)
        assert first.status_code == 200
        page = first.json()
        assert len(page["events"]) == 1 and page["has_more"]
        cursor = page["next_cursor"]
        second = httpx.get(url + f"/api/events?after={cursor}&limit=1", headers=headers).json()
        assert second["events"][0]["kind"] == "token.created"
        assert second["events"][0]["detail"] == {"name": "reader", "scope": "read"}
        admin.revoke_token(read.id)
        admin.revoke_token(read.id)
        final = httpx.get(url + f"/api/events?after={second['next_cursor']}", headers=root_headers).json()
        assert len(final["events"]) == 1 and final["events"][0]["kind"] == "token.revoked"
        assert not final["has_more"] and read.token not in json.dumps(final)
        assert httpx.get(url + "/api/events").status_code == 401
        for query in ("after=-1", "after=bad", "after=1&after=2", "limit=0", "limit=2001", "limit=1&limit=2"):
            reply = httpx.get(url + "/api/events?" + query, headers=root_headers)
            assert reply.status_code == 400 and reply.json()["error"]["code"] == "invalid_request"


def test_events_ride_snapshots_without_restoring_secrets(service: ControlService, tmp_path: Path) -> None:
    service.put_user("alice")
    key = generate_key_pair()
    service.backups.register_device(DeviceKeyCreate(name="laptop", public_key=key.public))
    before = service.events.list(limit=2000)
    snapshot = decrypt_snapshot(service.snapshot(), [key.secret])
    path = tmp_path / "snapshot.db"
    path.write_bytes(snapshot.database)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == len(before.events)
        assert db.execute("SELECT COUNT(*) FROM secrets").fetchone()[0] == 0
    restored_path = tmp_path / "restored.db"
    stage_restored_database(snapshot.database, restored_path, fleet_id=FLEET_ID, version="4.0.0")
    restored = ControlService(restored_path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert restored.events.list(limit=2000) == before
    finally:
        restored.close()


@pytest.mark.parametrize("case", ["user", "node", "token"])
def test_event_contract_refuses_credentials_raw_errors_and_device_values(case: str) -> None:
    from pydantic import ValidationError

    from meridian.core.fleet_events import FleetEvent

    cases = {
        "user": ("user.updated", {"fields": ["expire_at"], "uuid": "5b0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00"}),
        "node": ("node.apply_failed", {"reason": "apply_failed", "message": "raw error at 198.51.100.7"}),
        "token": ("token.created", {"name": "reader", "scope": "read", "token": "credential-value"}),
    }
    kind, detail = cases[case]
    with pytest.raises(ValidationError):
        FleetEvent.model_validate(
            {"id": 1, "time": NOW, "kind": kind, "actor": "root", "subject": "alice", "detail": detail}
        )


def test_request_actors_stay_separate_between_concurrent_tokens(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as admin:
        first, second = admin.create_token("writer-a", "admin"), admin.create_token("writer-b", "admin")

        def create(name: str, token: str) -> None:
            with ControlClient(url, token) as client:
                client.put_user(name)

        with ThreadPoolExecutor(max_workers=2) as executor:
            a = executor.submit(create, "alice", first.token)
            b = executor.submit(create, "bob", second.token)
            a.result(timeout=5)
            b.result(timeout=5)
        actors = {event.subject: event.actor for event in admin.events().events if event.kind == "user.created"}
        assert actors == {"alice": "writer-a", "bob": "writer-b"}


def test_periodic_traffic_reset_and_restoration_are_system_events_once_per_period(
    service: ControlService, clock: list[float]
) -> None:
    user = service.create_user(UserCreate(username="alice", traffic_limit_bytes=10, traffic_limit_strategy="day"))
    report = StatsReport.model_validate(
        {
            "type": "stats",
            "counter_epoch": "5b0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00",
            "seq": 1,
            "collected_at": NOW,
            "users": [{"user_id": user.user_id, "uplink_bytes": 10, "downlink_bytes": 0}],
        }
    )
    service.record_stats("main", report)
    clock[0] += 86400
    service.enforce()
    count = len(service.events.list(limit=2000).events)
    service.enforce()
    assert len(service.events.list(limit=2000).events) == count
    resets = [event for event in service.events.list(limit=2000).events if event.kind == "user.traffic_reset"]
    assert len(resets) == 2 and all(event.actor == "system" for event in resets)
    assert service.events.list(limit=2000).events[-1].kind == "user.restored"


def test_missing_report_never_announces_apply_recovery_and_state_survives_a_restart(
    service: ControlService, tmp_path: Path, clock: list[float]
) -> None:
    service.put_node_spec("main", _spec())
    failed = _report(_document(service), failed="core-unit")
    service.report("main", ObservedStateReport.model_validate(failed), failed)
    service.enforce()
    service._db.execute("UPDATE nodes SET report = NULL WHERE node_id = 'main'")
    service.enforce()
    before = service.events.list(limit=2000)
    restarted = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0", clock=lambda: clock[0])
    try:
        restarted.enforce()
        assert restarted.events.list(limit=2000) == before
    finally:
        restarted.close()


def test_events_command_help_exposes_cursor_limit_and_json() -> None:
    result = CliRunner().invoke(app, ["fleet", "events", "--help"])
    assert result.exit_code == 0
    assert "--since" in result.output and "--limit" in result.output and "--json" in result.output


def test_events_command_json_continues_the_cursor_and_invalid_flags_have_an_envelope(
    tmp_path: Path, tmp_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from meridian.cluster import ClusterConfig, PanelConfig
    from meridian.commands import fleet_events
    from meridian.console import set_json_mode

    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as admin:
        admin.put_user("alice")
        admin.put_user("bob")
        cluster = ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN))
        monkeypatch.setattr(fleet_events, "load_topology_cluster", lambda: cluster)
        runner = CliRunner()
        try:
            result = runner.invoke(app, ["fleet", "events", "--since", "1", "--limit", "1", "--json"])
            assert result.exit_code == 0, result.output
            page = json.loads(result.stdout)["data"]
            assert page["events"][0]["subject"] == "bob" and page["next_cursor"] == 2
            result = runner.invoke(app, ["fleet", "events", "--limit", "۱", "--json"])
            assert result.exit_code == 0, result.output
            for option, value in (("--since", "bad"), ("--since", "-1"), ("--limit", "2001"), ("--limit", "0")):
                result = runner.invoke(app, ["fleet", "events", option, value, "--json"])
                assert result.exit_code == 2
                payload = json.loads(result.stdout)
                assert payload["command"] == "fleet.events" and payload["status"] == "failed"
        finally:
            set_json_mode(False)


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_event_human_views_translate_the_kinds_and_changed_fields(
    tmp_path: Path, tmp_home: Path, monkeypatch: pytest.MonkeyPatch, locale: str
) -> None:
    from meridian import i18n
    from meridian.cluster import ClusterConfig, PanelConfig
    from meridian.commands import fleet_events
    from meridian.console import set_json_mode
    from meridian.core.fleet_events import FLEET_EVENT_KINDS

    monkeypatch.setattr(i18n, "_requested", None)
    monkeypatch.setattr(i18n, "_current", "en")
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as admin:
        admin.put_user("alice")
        admin.update_user("alice", UserUpdate(traffic_limit_bytes=100))
        cluster = ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN))
        monkeypatch.setattr(fleet_events, "load_topology_cluster", lambda: cluster)
        try:
            result = CliRunner().invoke(app, ["--lang", locale, "fleet", "events"])
            assert result.exit_code == 0, result.output
            assert i18n.t("events.title") in result.output and "alice" in result.output
            assert "traffic_limit_bytes" not in result.output
            assert all(i18n.t(f"events.kind.{kind}") for kind in FLEET_EVENT_KINDS)
        finally:
            set_json_mode(False)


def test_expiring_people_token_events_use_registry_time_and_keep_only_names_and_scopes(
    service: ControlService, clock: list[float]
) -> None:
    from meridian.control.errors import ServiceError
    from meridian.control.models import TokenCreate
    from meridian.control.tokens import ROOT

    issued = service.tokens.issue(TokenCreate(name="people-tool", scope="people", expires_in_days=1), issuer=ROOT)
    created = service.events.list().events[-1]
    assert created.kind == "token.created" and created.time == issued.created_at == NOW
    assert created.to_data()["detail"] == {"name": "people-tool", "scope": "people"}
    assert issued.expires_at == "2026-11-02T12:00:00Z"
    clock[0] += 86400
    with pytest.raises(ServiceError, match="Authentication required"):
        service.tokens.authenticate(issued.token)
    assert service.tokens.get(issued.id).last_used_at is None
    assert len(service.events.list().events) == 1
    service.tokens.revoke(issued.id)
    revoked = service.events.list().events[-1]
    assert revoked.kind == "token.revoked" and revoked.time == format_timestamp(clock[0])
    assert revoked.detail == created.detail
    assert issued.token not in json.dumps(service.events.list().to_data())


def test_an_event_failure_rolls_back_expiring_token_issuance(service: ControlService) -> None:
    from meridian.control.models import TokenCreate
    from meridian.control.tokens import ROOT

    before = service.events.list()
    service._store.create(
        "CREATE TRIGGER refuse_token_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'event refused'); END;"
    )
    with pytest.raises(sqlite3.IntegrityError, match="event refused"):
        service.tokens.issue(TokenCreate(name="people-tool", scope="people", expires_in_days=1), issuer=ROOT)
    assert service.tokens.list_tokens().tokens == []
    assert service.events.list() == before
