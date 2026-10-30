"""One operator notice reaches native clients and the connection page."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from meridian.control.client import ControlClient, add_mutation_listener
from meridian.control.models import DeviceKeyCreate, ServiceSettings, UserUpdate
from meridian.control.restore import stage_restored_database
from meridian.control.service import ControlService
from meridian.control.snapshot import decrypt_snapshot, generate_key_pair
from meridian.core.notices import Notice, NoticeWrite
from tests.support.control_service import FLEET_ID
from tests.test_control_ui_http import ADMIN_TOKEN, Fleet
from tests.test_control_ui_http import fleet as fleet


def test_notice_preview_and_save_share_the_operator_api(fleet: Fleet) -> None:
    user = fleet.service.put_user("person")
    request = {"notice": {"text": "Hello {name}", "until": None}, "for_user": "person", "confirm": False}
    preview = fleet.call("PUT", "/api/notice", request, bearer=ADMIN_TOKEN)
    assert preview.status == 200
    assert preview.body["preview"] == "Hello person"
    assert "announce" not in fleet.service.subscription(user.sub_token).headers

    saved = fleet.call("PUT", "/api/notice", {**request, "confirm": True}, bearer=ADMIN_TOKEN)
    assert saved.status == 200
    assert saved.body["changed"] is True
    response = fleet.service.subscription(user.sub_token)
    assert response.headers["announce"].startswith("base64:")
    assert base64.b64decode(response.headers["announce"].removeprefix("base64:")).decode() == "Hello person"
    page = fleet.service.pages.render(user.sub_token)
    assert "Hello person" in page.body


@pytest.mark.parametrize(
    "text",
    [
        "",
        "x" * 501,
        "Two\nparagraphs",
        "Hello\r\nthere",
        "\u202eHidden",
        "One\u2029Two",
        "{name!r}",
        "{name:>20}",
        "{name.__class__}",
        "{unknown}",
        "{{name}}",
        "{",
        "}",
    ],
)
def test_bad_notices_are_refused_before_storage_and_name_the_allowed_fields(fleet: Fleet, text: str) -> None:
    reply = fleet.call("PUT", "/api/notice", {"notice": {"text": text}, "confirm": True}, bearer=ADMIN_TOKEN)
    assert reply.status == 400
    if "{" in text or "}" in text:
        assert all(
            "{" + name + "}" in reply.body["error"]["message"]
            for name in ("name", "expires", "days_left", "used", "quota")
        )
    assert fleet.service.notices.read() is None and fleet.service.events.list().events == []


def test_notice_substitutions_encode_unicode_after_rendering_each_person(fleet: Fleet) -> None:
    person = fleet.service.put_user("person")
    other = fleet.service.put_user("other")
    expiry = datetime.fromtimestamp(fleet.clock.now, UTC) + timedelta(days=2)
    fleet.service.update_user(
        "person", UserUpdate(expire_at=expiry.strftime("%Y-%m-%dT%H:%M:%SZ"), traffic_limit_bytes=1024**3)
    )
    notice = Notice(text="پیام 测试 {name}: {expires}, {days_left}, {used}/{quota}")
    fleet.service.notices.update(NoticeWrite(notice=notice, confirm=True))
    decoded = base64.b64decode(
        fleet.service.subscription(person.sub_token).headers["announce"].removeprefix("base64:")
    ).decode()
    assert decoded == f"پیام 测试 person: {expiry:%Y-%m-%d %H:%M UTC}, 2, 0 B/1 GiB"
    assert (
        "other: No expiry, —, 0 B/Unlimited"
        in base64.b64decode(
            fleet.service.subscription(other.sub_token).headers["announce"].removeprefix("base64:")
        ).decode()
    )


def test_notice_expiry_stops_headers_and_page_delivery_at_the_instant(fleet: Fleet) -> None:
    person = fleet.service.put_user("person")
    until = datetime.fromtimestamp(fleet.clock.now + 1, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    fleet.service.notices.update(NoticeWrite(notice=Notice(text="Short notice", until=until), confirm=True))
    assert "announce" in fleet.service.subscription(person.sub_token).headers
    fleet.clock.now += 1
    assert "announce" not in fleet.service.subscription(person.sub_token).headers
    assert "Short notice" not in fleet.service.pages.render(person.sub_token).body
    assert "notice" not in json.loads(fleet.service.subscription(person.sub_token, "info").body)
    state = fleet.service.notices.show()
    assert state.notice is not None and state.active is False and state.preview == ""


def test_notice_changes_record_actor_without_text_and_no_ops_or_previews_add_no_event(fleet: Fleet) -> None:
    token = fleet.service.tokens.issue
    from meridian.control.models import TokenCreate
    from meridian.control.tokens import ROOT

    issued = token(TokenCreate(name="operator", scope="admin"), issuer=ROOT)
    request = {"notice": {"text": "Sensitive operator message"}, "confirm": True}
    assert fleet.call("PUT", "/api/notice", request, bearer=issued.token).status == 200
    assert fleet.call("PUT", "/api/notice", request, bearer=issued.token).body["changed"] is False
    assert fleet.call("PUT", "/api/notice", {**request, "confirm": False}, bearer=issued.token).body["changed"] is False
    facts = [event for event in fleet.service.events.list().events if event.kind == "notice.changed"]
    assert len(facts) == 1 and facts[0].actor == "operator" and facts[0].detail.to_data() == {}
    assert "Sensitive" not in json.dumps([event.to_data() for event in facts])


def test_notice_preview_is_a_read_for_backup_hooks_and_storage(fleet: Fleet) -> None:
    calls: list[str] = []
    stop = add_mutation_listener(calls.append)
    before = fleet.service.notices.show()
    try:
        with ControlClient(fleet.url, ADMIN_TOKEN) as client:
            preview = client.set_notice(NoticeWrite(notice=Notice(text="Preview")))
            assert preview.preview == "Preview" and calls == []
            client.set_notice(NoticeWrite(notice=Notice(text="Saved"), confirm=True))
            assert calls == [fleet.url]
    finally:
        stop()
    assert before.notice is None


def test_owner_can_edit_notice_but_people_and_read_tokens_cannot(fleet: Fleet) -> None:
    from meridian.control.models import TokenCreate
    from meridian.control.tokens import ROOT

    assert fleet.set_up().status == 200
    request = {"notice": {"text": "Owner message"}, "confirm": True}
    assert fleet.ui("PUT", "/api/notice", request).status == 200
    assert fleet.service.events.list().events[-1].actor == "owner"
    for scope in ("read", "people", "approve"):
        token = fleet.service.tokens.issue(TokenCreate(name=scope, scope=scope), issuer=ROOT)
        assert fleet.call("PUT", "/api/notice", request, bearer=token.token).status == 403


def test_notice_survives_reviewed_settings_and_encrypted_snapshot_restore(fleet: Fleet, tmp_path: Path) -> None:
    key = generate_key_pair()
    fleet.service.backups.register_device(DeviceKeyCreate(name="test device", public_key=key.public))
    fleet.service.notices.update(NoticeWrite(notice=Notice(text="Persisted {name}"), confirm=True))
    fleet.service.put_settings(ServiceSettings(profile_title="Changed title"))
    assert fleet.service.notices.show().notice.text == "Persisted {name}"
    blob = fleet.service.snapshot()
    assert b"Persisted" not in blob
    snapshot = decrypt_snapshot(blob, [key.secret])
    path = tmp_path / "restored.db"
    stage_restored_database(snapshot.database, path, fleet_id=FLEET_ID, version="4.0.0")
    restored = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert restored.notices.show().notice.text == "Persisted {name}"
        assert restored.events.list().events[-1].kind == "notice.changed"
    finally:
        restored.close()


def test_failed_notice_event_rolls_back_the_message(fleet: Fleet, monkeypatch: pytest.MonkeyPatch) -> None:
    fleet.service.notices.update(NoticeWrite(notice=Notice(text="Before"), confirm=True))
    before = fleet.service.events.list().events

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("event write failed")

    monkeypatch.setattr(fleet.service.events, "append", refuse)
    with pytest.raises(RuntimeError, match="event write failed"):
        fleet.service.notices.update(NoticeWrite(notice=Notice(text="After"), confirm=True))
    assert fleet.service.notices.show().notice.text == "Before"
    assert fleet.service.events.list().events == before


@pytest.mark.parametrize("until", ["2020-01-01T00:00:00Z", "2027-02-30T00:00:00Z", "2027-01-01T00:00:00"])
def test_notice_rejects_past_impossible_or_unzoned_expiry(fleet: Fleet, until: str) -> None:
    reply = fleet.call(
        "PUT", "/api/notice", {"notice": {"text": "Message", "until": until}, "confirm": True}, bearer=ADMIN_TOKEN
    )
    assert reply.status == 400 and fleet.service.notices.read() is None
