"""Operator language preferences translate only subscription presentation."""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path
from urllib.parse import unquote

import pytest
import yaml

from meridian.control.formats.entries import PLACEHOLDER_UUID, SubscriptionUser
from meridian.control.formats.selector import DocumentFormat
from meridian.control.models import DeviceKeyCreate, ServiceSettings, UserUpdate
from meridian.control.restore import stage_restored_database
from meridian.control.service import SCHEMA_VERSION, ControlService
from meridian.control.snapshot import decrypt_snapshot, generate_key_pair
from meridian.control.subscriptions import render_subscription
from meridian.core.languages import Language, LanguageSettings
from tests.support.control_service import FLEET_ID
from tests.test_control_subscription_corpus import FLEETS, SHORT_ID, STATUS_LANGUAGE, UUID
from tests.test_control_ui_http import ADMIN_TOKEN, Fleet
from tests.test_control_ui_http import fleet as fleet


def test_fleet_language_changes_inert_status_remarks(fleet: Fleet) -> None:
    user = fleet.service.put_user("person")
    reply = fleet.call("PUT", "/api/language", {"language": "ru"}, bearer=ADMIN_TOKEN)
    assert reply.status == 200
    reply = fleet.call("PATCH", "/api/users/person", {"status": "disabled"}, bearer=ADMIN_TOKEN)
    assert reply.status == 200 and reply.body["status"] == "disabled"
    links = unquote(base64.b64decode(fleet.service.subscription(user.sub_token).body).decode())
    assert "Подписка отключена" in links
    assert "00000000-0000-0000-0000-000000000000@0.0.0.0:1" in links


def test_person_language_override_is_optional_and_null_returns_to_the_fleet(fleet: Fleet) -> None:
    person = fleet.service.put_user("person")
    assert fleet.call("PUT", "/api/language", {"language": "ru"}, bearer=ADMIN_TOKEN).status == 200
    changed = fleet.call("PATCH", "/api/users/person", {"status": "disabled", "language": "zh"}, bearer=ADMIN_TOKEN)
    assert changed.status == 200 and changed.body["language"] == "zh"
    body = unquote(base64.b64decode(fleet.service.subscription(person.sub_token).body).decode())
    assert "订阅已停用" in body and "Подписка отключена" not in body
    cleared = fleet.call("PATCH", "/api/users/person", {"language": None}, bearer=ADMIN_TOKEN)
    assert cleared.status == 200 and cleared.body["language"] is None
    assert "Подписка отключена" in unquote(base64.b64decode(fleet.service.subscription(person.sub_token).body).decode())


@pytest.mark.parametrize("language", ["de", "RU", "ru-RU", "", 1])
def test_invalid_explicit_languages_are_refused(fleet: Fleet, language: object) -> None:
    fleet.service.put_user("person")
    assert fleet.call("PUT", "/api/language", {"language": language}, bearer=ADMIN_TOKEN).status == 400
    assert fleet.call("PATCH", "/api/users/person", {"language": language}, bearer=ADMIN_TOKEN).status == 400


@pytest.mark.parametrize("language", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize("status", ["disabled", "limited", "expired"])
@pytest.mark.parametrize("document", ["base64", "mihomo", "stash", "singbox", "xray-json"])
def test_every_format_keeps_localized_status_entries_inert(
    language: Language, status: str, document: DocumentFormat
) -> None:
    user = SubscriptionUser(username="person", uuid=UUID, short_id=SHORT_ID, status=status, language=language)
    response = render_subscription(user, FLEETS["single"].endpoints, document, profile_title="Meridian")
    body = base64.b64decode(response.body).decode() if document == "base64" else response.body
    assert UUID not in body and SHORT_ID not in body
    expected = STATUS_LANGUAGE[language][status]
    if document == "base64":
        assert [unquote(line.split("#", 1)[1]) for line in body.splitlines()] == expected
        assert all(PLACEHOLDER_UUID in line and "@0.0.0.0:1" in line for line in body.splitlines())
    elif document in ("mihomo", "stash"):
        proxies = yaml.safe_load(body)["proxies"]
        assert [proxy["name"] for proxy in proxies] == expected
        assert all(
            proxy["server"] == "0.0.0.0" and proxy["port"] == 1 and proxy["uuid"] == PLACEHOLDER_UUID
            for proxy in proxies
        )
    elif document == "singbox":
        proxies = [outbound for outbound in json.loads(body)["outbounds"] if outbound["type"] == "vless"]
        assert [proxy["tag"] for proxy in proxies] == expected
        assert all(
            proxy["server"] == "0.0.0.0" and proxy["server_port"] == 1 and proxy["uuid"] == PLACEHOLDER_UUID
            for proxy in proxies
        )
    else:
        configs = json.loads(body)
        assert [config["remarks"] for config in configs] == expected
        assert all(not any(outbound["protocol"] == "vless" for outbound in config["outbounds"]) for config in configs)


def test_language_and_person_preference_survive_settings_apply_and_snapshot(fleet: Fleet, tmp_path: Path) -> None:
    person = fleet.service.put_user("person")
    fleet.service.languages.set(LanguageSettings(language="ru"))
    fleet.service.update_user("person", UserUpdate(status="disabled", language="fa"))
    fleet.service.put_settings(ServiceSettings(profile_title="Reviewed title"))
    assert fleet.service.languages.read().language == "ru" and fleet.service.get_user("person").language == "fa"
    key = generate_key_pair()
    fleet.service.backups.register_device(DeviceKeyCreate(name="test", public_key=key.public))
    snapshot = decrypt_snapshot(fleet.service.snapshot(), [key.secret])
    path = tmp_path / "restored.db"
    stage_restored_database(snapshot.database, path, fleet_id=FLEET_ID, version="4.0.0")
    restored = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert restored.languages.read().language == "ru" and restored.get_user("person").language == "fa"
        assert "اشتراک غیرفعال است" in unquote(base64.b64decode(restored.subscription(person.sub_token).body).decode())
    finally:
        restored.close()


def test_schema_16_database_migrates_to_nullable_person_language(tmp_path: Path) -> None:
    path = tmp_path / "control.db"
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    service.put_user("person")
    service.close()
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE users DROP COLUMN language")
        db.execute("UPDATE meta SET value = '16' WHERE key = 'schema_version'")
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert service.get_user("person").language is None and service.languages.read().language == "auto"
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == (
                str(SCHEMA_VERSION),
            )
    finally:
        service.close()
