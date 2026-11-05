"""Private person assignments are reviewed service data, never applied account settings."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from meridian.control.models import ServiceSettings, TokenCreate
from meridian.control.service import ControlService
from meridian.control.tokens import ROOT
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID
from tests.support.token_service import serve_tokens, token_request
from tests.test_control_subscription_corpus import FLEETS
from tests.test_delivery_policies import delivery_data


def test_person_assignment_has_a_private_review_and_changes_live_delivery(tmp_path: Path) -> None:
    with serve_tokens(tmp_path) as fleet:
        user = fleet.service.put_user("person")
        for index, endpoint in enumerate(FLEETS["fleet"].endpoints):
            fleet.service.put_endpoint(f"entry-{index}", endpoint)
        fleet.service.put_settings(ServiceSettings.model_validate({"profile_title": "Meridian", **delivery_data()}))
        url = fleet.url + "/api/users/person/delivery-policy"
        status, state = token_request(url, token=ADMIN_TOKEN)
        assert status == 200
        assert state["override"] is None and state["effective"]["policy"]["id"] == "work"
        before_events = fleet.service.events.list().events
        status, preview = token_request(url, "PUT", {"policy": "full"}, ADMIN_TOKEN)
        assert status == 200 and not preview["confirmed"]
        assert fleet.service.get_user("person").delivery_policy is None
        assert preview["before"]["effective"]["policy"]["id"] == "work"
        assert preview["after"]["effective"]["policy"]["id"] == "full"
        status, changed = token_request(
            url, "PUT", {"policy": "full", "confirm": True, "review_hash": preview["review_hash"]}, ADMIN_TOKEN
        )
        assert status == 200 and changed["confirmed"]
        assert fleet.service.get_user("person").delivery_policy == "full"
        assert "domain:example.test" not in fleet.service.subscription(user.sub_token, "json").body
        assert fleet.service.events.list().events == before_events
        token = fleet.service.tokens.issue(TokenCreate(name="Read accounts", capabilities=["people.read"]), issuer=ROOT)
        status, visible = token_request(fleet.url + "/api/users/person", token=token.token)
        assert status == 200 and visible.get("delivery_policy") is None
        assert token_request(url, token=token.token)[0] == 403
        assert token_request(url, "PUT", {"policy": "work"}, token.token)[0] == 403


def test_person_reviews_reject_membership_or_catalog_changes_and_write_nothing(tmp_path: Path) -> None:
    with serve_tokens(tmp_path) as fleet:
        fleet.service.put_user("person")
        settings = ServiceSettings.model_validate({"profile_title": "Meridian", **delivery_data()})
        fleet.service.put_settings(settings)
        url = fleet.url + "/api/users/person/delivery-policy"
        status, preview = token_request(url, "PUT", {"policy": "full"}, ADMIN_TOKEN)
        assert status == 200
        fleet.service.put_settings(ServiceSettings.model_validate({**settings.to_data(), "default_policy": "ru-split"}))
        status, result = token_request(
            url, "PUT", {"policy": "full", "confirm": True, "review_hash": preview["review_hash"]}, ADMIN_TOKEN
        )
        assert status == 409 and result["error"]["code"] == "stale_delivery_policy"
        assert fleet.service.get_user("person").delivery_policy is None


def test_schema_24_migrates_in_one_step_and_overrides_survive_applies_and_reissue(tmp_path: Path) -> None:
    from meridian.control.schema import SCHEMA_VERSION
    from meridian.core.person_delivery_policy import PersonDeliveryPolicyWrite
    from tests.support.control_service import drop_person_display_name_schema

    path = tmp_path / "control.db"
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    user = service.put_user("person")
    service.close()
    with sqlite3.connect(path) as db:
        drop_person_display_name_schema(db)
        for name in ("person_delivery_policy_insert", "person_delivery_policy_update", "person_delivery_policy_delete"):
            db.execute(f"DROP TRIGGER IF EXISTS {name}")
        db.execute("DROP TABLE IF EXISTS person_delivery_policies")
        db.execute("UPDATE meta SET value = '24' WHERE key = 'schema_version'")
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert service._store.meta("schema_version") == str(SCHEMA_VERSION)
        before = service._store.row("SELECT version FROM people_versions WHERE user_id = ?", (user.user_id,))
        assert before is not None
        preview = service.delivery.set_person("person", PersonDeliveryPolicyWrite(policy="full"))
        service.delivery.set_person(
            "person", PersonDeliveryPolicyWrite(policy="full", confirm=True, review_hash=preview.review_hash)
        )
        after = service._store.row("SELECT version FROM people_versions WHERE user_id = ?", (user.user_id,))
        assert after is not None and after[0] == before[0] + 1
        service.put_user("person")
        service.put_settings(ServiceSettings(profile_title="Updated", client_routing="ru-split"))
        changed = service.reissue_user("person")
        assert changed.user_id == user.user_id and changed.delivery_policy == "full"
    finally:
        service.close()
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert service.get_user("person").delivery_policy == "full"
        assert service.delivery.person("person").effective.policy.id == "full"
        service.delete_user("person")
        assert service._store.rows("SELECT * FROM person_delivery_policies") == []
    finally:
        service.close()
