"""Private person metadata: ownership, permissions, snapshots, and public projections."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.client import run_list, run_show
from meridian.commands.client_limits import run_set
from meridian.console import set_json_mode
from meridian.control.client import ControlClient, ControlServiceScopeError
from meridian.control.models import DeviceKeyCreate, ServiceSettings, UserUpdate
from meridian.control.restore import stage_restored_database
from meridian.control.service import SCHEMA_VERSION, ControlService
from meridian.control.snapshot import decrypt_snapshot, generate_key_pair
from meridian.core.person_metadata import parse_tag_option
from meridian.core.schema import schema_model
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID, drop_person_metadata_schema, serve_control
from tests.support.node_commands import command_node_spec
from tests.support.topology import single_exit_intent

NOTE = "Private invitation\n<em>Keep this as text</em> https://example.com/help"
TAG = "TRIAL_7"


def test_metadata_edits_preserve_other_fields_and_apply_never_overwrites_them(tmp_path: Path) -> None:
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    try:
        original = service.put_user("sample")
        service.put_node_spec("main", command_node_spec())
        revision = service.node_status("main").revision
        updated = service.update_user("sample", UserUpdate.model_validate({"note": NOTE, "tag": TAG}))
        assert (updated.note, updated.tag) == (NOTE, TAG)
        assert service.node_status("main").revision == revision
        assert (updated.uuid, updated.sub_token, updated.status) == (original.uuid, original.sub_token, "active")
        service.update_user("sample", UserUpdate(traffic_limit_bytes=1234))
        applied = service.put_user("sample")
        assert (applied.note, applied.tag, applied.traffic_limit_bytes) == (NOTE, TAG, 1234)
        service.update_user("sample", UserUpdate.model_validate({"note": ""}))
        assert service.get_user("sample").tag == TAG
        cleared = service.update_user("sample", UserUpdate.model_validate({"tag": ""}))
        assert (cleared.note, cleared.tag) == ("", "")
    finally:
        service.close()


@pytest.mark.parametrize(
    "fields",
    [{"note": "x" * 501}, {"note": "secret\x00text"}, {"tag": "trial"}, {"tag": "A-B"}, {"tag": "X" * 17}],
    ids=["long-note", "control-character", "lowercase-tag", "tag-punctuation", "long-tag"],
)
def test_invalid_metadata_is_refused_without_echoing_private_values(fields: dict[str, str], tmp_path: Path) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        before = admin.put_user("sample")
        reply = httpx.patch(f"{url}/api/users/sample", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}, json=fields)
        assert reply.status_code == 400
        assert next(iter(fields.values())) not in reply.text
        assert admin.get_user("sample") == before


@pytest.mark.parametrize("scope", ["read", "propose", "approve", "people", "admin"])
def test_metadata_is_visible_and_editable_only_to_people_or_admin(scope: str, tmp_path: Path) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        admin.put_user("sample")
        admin.update_user("sample", UserUpdate.model_validate({"note": NOTE, "tag": TAG}))
        token = admin.create_token("Test desk", scope)
        with ControlClient(url, token.token, report_mutations=False) as reader:
            for record in (reader.get_user("sample"), reader.list_users()[0]):
                assert record is not None
                expected = (NOTE, TAG) if scope in ("people", "admin") else ("", "")
                assert (record.note, record.tag) == expected
            if scope in ("people", "admin"):
                assert reader.update_user("sample", UserUpdate.model_validate({"note": ""})).tag == TAG
            else:
                with pytest.raises(ControlServiceScopeError):
                    reader.update_user("sample", UserUpdate.model_validate({"note": ""}))


def test_metadata_rides_encrypted_snapshots_and_restoration(tmp_path: Path) -> None:
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    key = generate_key_pair()
    try:
        service.put_user("sample")
        service.update_user("sample", UserUpdate.model_validate({"note": NOTE, "tag": TAG}))
        service.backups.register_device(DeviceKeyCreate(name="Test laptop", public_key=key.public))
        snapshot = decrypt_snapshot(service.snapshot(), [key.secret])
    finally:
        service.close()
    restored_path = tmp_path / "restored.db"
    stage_restored_database(snapshot.database, restored_path, fleet_id=FLEET_ID, version="4.0.0")
    restored = ControlService(restored_path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        person = restored.get_user("sample")
        assert person is not None and (person.note, person.tag) == (NOTE, TAG)
    finally:
        restored.close()


def test_schema_step_fifteen_adds_empty_metadata_to_existing_people(tmp_path: Path) -> None:
    database = tmp_path / "control.db"
    service = ControlService(database, fleet_id=FLEET_ID, version="4.0.0")
    original = service.put_user("sample")
    service.close()
    with sqlite3.connect(database) as db:
        drop_person_metadata_schema(db)
        db.execute("UPDATE meta SET value = '14' WHERE key = 'schema_version'")
    migrated = ControlService(database, fleet_id=FLEET_ID, version="4.0.0")
    try:
        person = migrated.get_user("sample")
        assert person is not None and (person.note, person.tag, person.uuid) == ("", "", original.uuid)
        assert migrated._store.meta("schema_version") == str(SCHEMA_VERSION)
    finally:
        migrated.close()


def test_metadata_never_enters_subscriptions_connection_pages_or_events(tmp_path: Path) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        admin.put_settings(ServiceSettings(profile_title="Test fleet", public_url="https://198.51.100.1/connect"))
        user = admin.put_user("sample")
        admin.update_user("sample", UserUpdate.model_validate({"note": NOTE, "tag": TAG}))
        for suffix in ("", "/json", "/mihomo", "/singbox", "/info", "/config.json", "/manifest.webmanifest"):
            response = httpx.get(f"{url}/sub/{user.sub_token}{suffix}")
            assert response.status_code == 200
            assert NOTE not in response.text and TAG not in response.text
            if suffix in ("/info", "/config.json"):
                assert '"note"' not in response.text and '"tag"' not in response.text
        html = httpx.get(f"{url}/sub/{user.sub_token}", headers={"Accept": "text/html"}).text
        assert NOTE not in html and TAG not in html
        with sqlite3.connect(tmp_path / "control.db") as db:
            events = db.execute("SELECT kind, detail FROM events ORDER BY id").fetchall()
        assert events == [("user.created", "{}")]


def test_client_set_show_and_tag_filter_include_console_people(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        cluster = ClusterConfig(
            panel=PanelConfig(url=url, api_token=ADMIN_TOKEN, server_ip="198.51.100.1"),
            topology_intent=single_exit_intent(users=["declared"]),
        )
        admin.put_user("declared")
        admin.put_user("sample")
        set_json_mode(True)
        try:
            with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
                run_set("sample", limit=None, reset=None, expires=None, note=NOTE, tag=TAG)
                payload = json.loads(capsys.readouterr().out)
                schema_model("client-set-envelope").model_validate(payload)
                assert (payload["data"]["client"]["note"], payload["data"]["client"]["tag"]) == (NOTE, TAG)
                run_show("sample")
                shown = json.loads(capsys.readouterr().out)
                assert shown["data"]["client"]["note"] == NOTE
                run_list(tag=TAG)
                listed = json.loads(capsys.readouterr().out)
                assert listed["data"]["summary"]["clients"] == 1
                assert listed["data"]["clients"][0]["username"] == "sample"
                assert listed["data"]["clients"][0]["note"] == NOTE
                assert listed["data"]["clients"][0]["declared"] is False
                run_set("sample", limit=None, reset=None, expires=None, note="", tag="none")
                cleared = json.loads(capsys.readouterr().out)["data"]["client"]
                assert (cleared["note"], cleared["tag"]) == ("", "")
        finally:
            set_json_mode(False)


def test_service_models_do_not_add_rejected_person_contact_fields() -> None:
    for fields in ({"email": "sample@example.com"}, {"telegram_id": "1000001"}):
        with pytest.raises(ValidationError):
            UserUpdate.model_validate(fields)


def test_patch_users_accepts_a_people_owned_metadata_edit(tmp_path: Path) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        admin.put_user("sample")
        token = admin.create_token("Test desk", "people")
        reply = httpx.patch(
            f"{url}/api/users",
            headers={"Authorization": f"Bearer {token.token}"},
            json={"username": "sample", "note": NOTE, "tag": TAG},
        )
        assert reply.status_code == 200
        assert (reply.json()["note"], reply.json()["tag"]) == (NOTE, TAG)


def test_migration_import_persists_metadata_and_retries_preserve_operator_edits(tmp_path: Path) -> None:
    from meridian.migrate.users import carry_users
    from meridian.remnawave import User

    source = User(
        username="sample",
        vless_uuid="3f2a9c4e-7b1d-4e6a-9c2f-1a2b3c4d5e6f",
        short_uuid="sampleShortUuid00",
        status="ACTIVE",
        traffic_limit_strategy="NO_RESET",
        description=NOTE,
        tag=TAG,
    )
    batch = carry_users([source], ["sample"]).batch
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert service.import_users(batch).created == ["sample"]
        person = service.get_user("sample")
        assert person is not None and (person.note, person.tag) == (NOTE, TAG)
        service.update_user("sample", UserUpdate(note="Operator edit", tag="CURRENT"))
        assert service.import_users(batch).unchanged == ["sample"]
        assert service.get_user("sample").note == "Operator edit"
        service.import_users(batch.model_copy(update={"replace": True}))
        assert service.get_user("sample").note == NOTE
    finally:
        service.close()


@pytest.mark.parametrize(
    ("typed", "stored"),
    [("family", "FAMILY"), ("Trial_2", "TRIAL_2"), ("none", ""), ("NONE", "NONE"), ("None", "NONE")],
    ids=["lowercase", "mixed", "clears", "uppercase-none-is-a-tag", "capitalised-none-is-a-tag"],
)
def test_cli_tags_are_saved_in_uppercase(typed: str, stored: str) -> None:
    """A demo's `--tag family` was refused, and the note sent in the same edit went with it."""
    assert parse_tag_option(typed) == stored
