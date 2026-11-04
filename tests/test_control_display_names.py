"""Service names survive topology edits and refuse ambiguity inside account writes."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.control.client import ControlClient
from meridian.control.errors import ServiceError
from meridian.control.models import (
    RevisionProposal,
    RevisionResult,
    ServiceSettings,
    UserCreate,
    UserImport,
    UserImportBatch,
    UserPatch,
    UserUpdate,
)
from meridian.control.schema import SCHEMA_VERSION
from meridian.control.service import ControlService
from meridian.control.tokens import ROOT
from meridian.control.ui_views import fleet_view
from meridian.control_access import ServiceAccessPanel
from meridian.core.clients import build_client_detail, build_client_record
from meridian.core.people_bulk import BulkAction, BulkCommitRequest, BulkPreviewRequest
from meridian.core.people_query import PeopleQuery
from meridian.handoff import user_handoff
from tests.support.control_service import (
    ADMIN_TOKEN,
    FLEET_ID,
    drop_person_display_name_schema,
    serve_control,
)

NOW = datetime.fromisoformat("2026-11-01T12:00:00Z").timestamp()


@pytest.fixture
def service(tmp_path: Path):
    instance = ControlService(tmp_path / "names.db", fleet_id=FLEET_ID, version="4.0.0", clock=lambda: NOW)
    yield instance
    instance.close()


def revision(service, names, status="applied"):
    result = service.revisions.propose(
        RevisionProposal(
            intent={"access": {"users": list(names), "display_names": names}},
            plan_hash="a" * 64,
            review_hash="b" * 64,
        ),
        caller=ROOT,
    )
    if status != "proposed":
        service.revisions.approve(result.number, caller=ROOT)
    if status in ("applied", "failed"):
        service.revisions.record_result(result.number, RevisionResult(status=status))


def imported(username, name, number):
    return UserImport(
        username=username,
        display_name=name,
        uuid=f"00000000-0000-4000-8000-{number:012d}",
        sub_token=f"sample-import-{number:04d}",
    )


@pytest.mark.parametrize("status", ["proposed", "approved", "failed"])
def test_migration_uses_only_the_latest_applied_names_and_joins_literal_username_keys(tmp_path, status):
    database = tmp_path / "migration.db"
    service = ControlService(database, fleet_id=FLEET_ID, version="4.0.0", clock=lambda: NOW)
    for name in ("sample", ".guest", "unnamed"):
        service.put_user(name)
    revision(service, {"sample": "Older label", "unnamed": "Removed label"})
    revision(service, {"sample": "Тест", ".guest": "样本", "missing": "Absent person"})
    revision(service, {"sample": "Unapplied label"}, status)
    service.close()
    with sqlite3.connect(database) as db:
        drop_person_display_name_schema(db)
        db.execute("UPDATE meta SET value = '25' WHERE key = 'schema_version'")
    migrated = ControlService(database, fleet_id=FLEET_ID, version="4.0.0", clock=lambda: NOW)
    try:
        assert {user.username: user.display_name for user in migrated.list_users()} == {
            "sample": "Тест",
            ".guest": "样本",
            "unnamed": "",
        }
        assert int(migrated._meta("schema_version")) == SCHEMA_VERSION
    finally:
        migrated.close()


def test_migration_without_an_applied_revision_keeps_the_username(tmp_path):
    database = tmp_path / "migration.db"
    service = ControlService(database, fleet_id=FLEET_ID, version="4.0.0", clock=lambda: NOW)
    service.put_user("sample")
    revision(service, {"sample": "Proposed label"}, "proposed")
    service.close()
    with sqlite3.connect(database) as db:
        drop_person_display_name_schema(db)
        db.execute("UPDATE meta SET value = '25' WHERE key = 'schema_version'")
    migrated = ControlService(database, fleet_id=FLEET_ID, version="4.0.0", clock=lambda: NOW)
    try:
        assert migrated.get_user("sample").display_name == ""
        assert int(migrated._meta("schema_version")) == SCHEMA_VERSION
    finally:
        migrated.close()


def test_display_only_creation_transliterates_around_usernames_and_other_display_names(service):
    service.put_user("TEST")
    service.create_user(UserCreate(username="holder", display_name="test-2"))
    created = service.create_user(UserCreate(display_name="Тест"))
    assert (created.username, created.display_name) == ("test-3", "Тест")
    assert created.user_id and created.uuid and created.sub_token


@pytest.mark.parametrize(
    "fields",
    [{}, {"display_name": ""}, {"display_name": " "}, {"username": None}, {"display_name": None}],
    ids=["missing", "empty", "spaces", "null-username", "null-name"],
)
def test_creation_requires_a_real_name_before_any_write(fields):
    with pytest.raises(ValidationError):
        UserCreate(**fields)


@pytest.mark.parametrize("name", ["Line\nbreak", "Comma,name", "x" * 101])
def test_service_display_names_keep_the_core_plain_text_bounds(name):
    for model, fields in ((UserCreate, {}), (UserUpdate, {}), (UserImport, imported("sample", "", 1).model_dump())):
        with pytest.raises(ValidationError):
            model.model_validate({**fields, "display_name": name})


@pytest.mark.parametrize(
    "fields, code",
    [
        ({"username": "sample"}, "user_exists"),
        ({"username": "SAMPLE"}, "name_conflict"),
        ({"username": "fresh", "display_name": "SAMPLE"}, "name_conflict"),
        ({"username": "fresh", "display_name": "STRASSE"}, "name_conflict"),
        ({"username": "STRASSE"}, "name_conflict"),
        ({"display_name": "STRASSE"}, "name_conflict"),
    ],
    ids=["exact-user", "folded-user", "name-user", "name-name", "user-name", "assigned-name"],
)
def test_creation_refuses_cross_field_casefold_clashes_without_accounts_or_events(service, fields, code):
    service.create_user(UserCreate(username="sample", display_name="Straße"))
    before = service.list_users()
    count = service._db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    with pytest.raises(ServiceError) as refused:
        service.create_user(UserCreate(**fields))
    assert (refused.value.status, refused.value.code) == (409, code)
    assert service.list_users() == before
    assert service._db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == count
    assert "Straße" not in str(refused.value) and "STRASSE" not in str(refused.value)


@pytest.mark.parametrize("name", ["SAMPLE", "STRASSE"])
def test_patch_clashes_roll_back_every_supplied_account_field(service, name):
    service.create_user(UserCreate(username="sample", display_name="Straße"))
    before = service.create_user(UserCreate(username="other", display_name="Other label"))
    with pytest.raises(ServiceError, match="Each person needs a different name") as refused:
        service.update_user("other", UserUpdate(display_name=name, status="disabled"))
    assert refused.value.code == "name_conflict"
    assert service.get_user("other") == before


def test_patch_omission_preserves_empty_clears_and_a_person_may_match_their_own_username(service):
    service.create_user(UserCreate(username="sample", display_name="Тест"))
    assert service.update_user("sample", UserPatch(username="sample", status="disabled").change).display_name == "Тест"
    assert (
        service.update_user("sample", UserPatch(username="sample", display_name="SAMPLE").change).display_name
        == "SAMPLE"
    )
    assert service.update_user("sample", UserPatch(username="sample", display_name="").change).display_name == ""
    assert service.people.query(PeopleQuery()).users[0].display_name == "sample"


@pytest.mark.parametrize("replace", [False, True], ids=["create", "replace"])
def test_import_carries_display_names_and_keeps_them_on_an_idempotent_replay(service, replace):
    if replace:
        service.put_user("sample")
    batch = UserImportBatch(users=[imported("sample", "样本", 1)], replace=replace)
    service.import_users(batch)
    user = service.get_user("sample")
    assert user.display_name == "样本"
    service.import_users(UserImportBatch(users=batch.users))
    assert service.get_user("sample") == user


@pytest.mark.parametrize(
    "source",
    ["existing", "batch", "username", "folded-user"],
    ids=["stored-name", "earlier-batch-name", "user-matches-name", "folded-user"],
)
def test_import_name_clash_rolls_back_the_whole_batch_and_events(service, source):
    users = [imported("first", "Straße", 1), imported("second", "STRASSE", 2)]
    if source == "existing":
        service.create_user(UserCreate(username="holder", display_name="STRASSE"))
        users = [imported("first", "Fresh label", 1), imported("second", "Straße", 2)]
    if source == "username":
        service.create_user(UserCreate(username="holder", display_name="Straße"))
        users = [imported("first", "Fresh label", 1), imported("STRASSE", "", 2)]
    if source == "folded-user":
        service.put_user("SECOND")
        users = [imported("first", "Fresh label", 1), imported("second", "Another label", 2)]
    before = service.list_users()
    count = service._db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    with pytest.raises(ServiceError) as refused:
        service.import_users(UserImportBatch(users=users))
    assert refused.value.code == "name_conflict"
    assert service.list_users() == before
    assert service._db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == count


def test_concurrent_creators_cannot_both_claim_the_same_folded_display_name(service):
    def create(index):
        try:
            return service.create_user(UserCreate(username=f"person-{index}", display_name="Shared label")).username
        except ServiceError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, (1, 2)))
    assert results.count("name_conflict") == 1
    assert len(service.list_users()) == 1


def test_query_bulk_pages_fleet_and_cli_all_read_the_column_while_declarations_stay(service):
    service.create_user(UserCreate(username="sample", display_name="Тест"))
    revision(service, {"sample": "Old topology label"})
    service.put_settings(ServiceSettings(profile_title="Meridian", public_url="https://198.51.100.1/connect"))
    assert not hasattr(service.people.intent(), "names")
    assert service.people.intent().declared == {"sample"}
    page = service.people.query(PeopleQuery(q="тест"))
    assert [(person.username, person.display_name, person.declared) for person in page.users] == [
        ("sample", "Тест", True)
    ]
    assert not service.people.query(PeopleQuery(q="Old topology label")).users
    review = service.bulk.preview(BulkPreviewRequest(query=PeopleQuery(q="тест"), action=BulkAction(action="disable")))
    assert review.people[0].display_name == "Тест"
    receipt = service.bulk.commit(BulkCommitRequest(preview_id=review.id, preview_hash=review.preview_hash))
    assert receipt.people[0].display_name == "sample"
    service.bulk.undo(receipt.id)
    stored_receipt = service._db.execute("SELECT body FROM bulk_receipts WHERE id = ?", (receipt.id,)).fetchone()[0]
    assert "Тест" not in stored_receipt and "Тест" not in receipt.model_dump_json()
    assert service.pages._display_name("sample") == "Тест"
    assert "Тест" in service.pages.render(service.get_user("sample").sub_token, "config.json").body
    assert fleet_view(service).display_names == {"sample": "Тест"}
    service.create_user(UserCreate(username="console", display_name="样本"))
    assert fleet_view(service).display_names["console"] == "样本"
    service.update_user("sample", UserUpdate(display_name=""))
    assert service.pages._display_name("sample") == "sample"
    assert "sample" not in fleet_view(service).display_names
    raw_events = json.dumps([tuple(row) for row in service._db.execute("SELECT * FROM events")], ensure_ascii=False)
    assert "Тест" not in raw_events and "样本" not in raw_events


@pytest.mark.parametrize(
    "scope", ["read", "people", "admin", "explicit"], ids=["fleet-read", "people", "admin", "people-read"]
)
def test_names_are_public_person_fields_while_notes_and_keys_keep_their_grants(tmp_path, scope):
    with serve_control(tmp_path, clock=lambda: NOW) as url, ControlClient(url, ADMIN_TOKEN) as admin:
        person = admin.create_user(UserCreate(username="sample", display_name="Тест"))
        admin.update_user("sample", UserUpdate(note="Private note", tag="GROUP"))
        grant = (
            admin.create_token("Name reader", capabilities=["people.read"])
            if scope == "explicit"
            else admin.create_token("Name reader", scope)
        )
        with ControlClient(url, grant.token) as reader:
            record = reader.get_user("sample")
            assert record.display_name == "Тест"
            assert reader.list_users()[0].display_name == "Тест"
            if scope in ("read", "explicit"):
                assert (record.note, record.tag, record.uuid, record.sub_token) == ("", "", "", "")
            else:
                assert record.note == "Private note" and record.uuid == person.uuid
        config = httpx.get(f"{url}/sub/{person.sub_token}/config.json").json()
        assert "Тест" in json.dumps(config, ensure_ascii=False)
        assert "Private note" not in json.dumps(config)
        with ServiceAccessPanel(url, ADMIN_TOKEN) as panel:
            user = panel.get_user("sample")
            assert user.display_name == "Тест"
            assert build_client_record(user).display_name == build_client_detail(user).display_name == "Тест"
            cluster = ClusterConfig(panel=PanelConfig(url="https://198.51.100.1/admin", sub_path="connect"))
            assert user_handoff(cluster, user).name == "Тест"


def test_undo_an_old_receipt_never_copies_its_display_name_back_into_storage(service):
    service.create_user(UserCreate(username="sample", display_name="Тест"))
    review = service.bulk.preview(BulkPreviewRequest(usernames=["sample"], action=BulkAction(action="disable")))
    receipt = service.bulk.commit(BulkCommitRequest(preview_id=review.id, preview_hash=review.preview_hash))
    legacy = receipt.model_dump()
    legacy["people"][0]["display_name"] = "Older receipt label"
    service._db.execute("UPDATE bulk_receipts SET body = ? WHERE id = ?", (json.dumps(legacy), receipt.id))
    assert service.bulk.receipt(receipt.id).people[0].display_name == "sample"
    undone = service.bulk.undo(receipt.id)
    assert undone.undo_people[0].display_name == "sample"
    stored = service._db.execute("SELECT body FROM bulk_receipts WHERE id = ?", (receipt.id,)).fetchone()[0]
    assert "Older receipt label" not in stored and "Тест" not in stored
