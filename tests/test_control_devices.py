"""Device caps, privacy, browser bypasses, and transaction boundaries."""

from __future__ import annotations

import base64
import hashlib
import hmac
import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote

import pytest
from pydantic import ValidationError

from meridian.control.devices import DEVICE_HMAC_SECRET, DeviceLedger
from meridian.control.models import ClientEndpoint, PrivacySettings, UserCreate, UserUpdate
from meridian.control.service import ControlService
from meridian.control.store import Store
from meridian.control.subscriptions import DEVICE_LIMIT_REMARKS
from tests.support.control_service import FLEET_ID, write_privacy

HWID = "device-00000001"


@pytest.fixture
def service(tmp_path: Path) -> Iterator[ControlService]:
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    service.put_user("alice")
    service.put_endpoint(
        "main",
        ClientEndpoint(
            remark="Working endpoint",
            protocol="reality",
            address="198.51.100.10",
            port=443,
            position=0,
            reality_public_key="public-key",
            reality_short_id="0123456789abcdef",
        ),
    )
    yield service
    service.close()


def fetch(service: ControlService, hwid: str | None = HWID, *, username: str = "alice", **options: object):
    user = service.get_user(username)
    assert user is not None
    headers = {"x-hwid": hwid} if hwid is not None else {}
    return service.subscription(user.sub_token, user_agent="v2RayTun/1.0", device_headers=headers, **options)  # type: ignore[arg-type]


def device_rows(tmp_path: Path) -> list[tuple[object, ...]]:
    with sqlite3.connect(tmp_path / "control.db") as db:
        return db.execute("SELECT * FROM devices ORDER BY user_id, device_key").fetchall()


class OnlyHwid:
    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed
        self.reads: list[str] = []

    def get(self, name: str) -> str | None:
        assert self.allowed and name == "x-hwid", f"Unexpected header read: {name}"
        self.reads.append(name)
        return HWID


def test_off_never_reads_device_headers_or_creates_a_fingerprint_key(service: ControlService, tmp_path: Path) -> None:
    user = service.get_user("alice")
    assert user is not None
    assert service.privacy() == PrivacySettings()

    response = service.subscription(user.sub_token, device_headers=OnlyHwid(allowed=False))

    assert "Working endpoint" in unquote(base64.b64decode(response.body).decode())
    assert not any(name.startswith("x-hwid") for name in response.headers)
    assert device_rows(tmp_path) == []
    with sqlite3.connect(tmp_path / "control.db") as db:
        assert db.execute("SELECT value FROM secrets WHERE name = ?", (DEVICE_HMAC_SECRET,)).fetchone() is None


def test_private_reads_only_hwid_and_stores_neither_details_nor_seen_times(
    service: ControlService, tmp_path: Path
) -> None:
    write_privacy(tmp_path / "control.db", PrivacySettings(device_limits="private"))
    user = service.get_user("alice")
    assert user is not None
    headers = OnlyHwid(allowed=True)

    for _ in range(2):
        service.subscription(user.sub_token, device_headers=headers)

    (row,) = device_rows(tmp_path)
    with sqlite3.connect(tmp_path / "control.db") as db:
        (key,) = db.execute("SELECT value FROM secrets WHERE name = ?", (DEVICE_HMAC_SECRET,)).fetchone()
    fingerprint = hmac.new(key, HWID.encode(), hashlib.sha256).hexdigest()
    assert row == (user.user_id, fingerprint, None, None, None, None, None, None)
    assert headers.reads == ["x-hwid", "x-hwid"]
    view = service.user_devices("alice").to_data()
    assert view["count"] == 1 and view["devices"] == [{"device_key": fingerprint}]


def test_full_truncates_each_detail_and_keeps_the_raw_hwid(service: ControlService, tmp_path: Path) -> None:
    write_privacy(tmp_path / "control.db", PrivacySettings(device_limits="full"))
    user = service.get_user("alice")
    assert user is not None
    headers = {
        "x-hwid": HWID,
        "x-device-os": "p" * 700,
        "x-ver-os": "v" * 700,
        "x-device-model": "m" * 700,
        "user-agent": "a" * 700,
        "x-forwarded-for": "198.51.100.20",
    }

    service.subscription(user.sub_token, device_headers=headers)

    view = service.user_devices("alice").to_data()
    device = view["devices"][0]
    assert device["device_key"] == HWID
    assert [device[name] for name in ("platform", "os_version", "model", "user_agent")] == [c * 512 for c in "pvma"]
    assert device["first_seen_at"] == device["last_seen_at"] and device["first_seen_at"] is not None
    assert "198.51.100.20" not in repr(device_rows(tmp_path))


@pytest.mark.parametrize("level", ["private", "full"])
def test_known_devices_still_work_above_a_lowered_cap_and_null_restores_the_default(
    service: ControlService,
    tmp_path: Path,
    level: str,
) -> None:
    write_privacy(tmp_path / "control.db", PrivacySettings(device_limits=level, default_device_limit=3))  # type: ignore[arg-type]
    fetch(service, "device-00000001")
    fetch(service, "device-00000002")
    service.update_user("alice", UserUpdate(device_limit=1))
    assert "x-hwid-limit" not in fetch(service, "device-00000001").headers
    assert "x-hwid-limit" in fetch(service, "device-00000003").headers
    assert service.user_devices("alice").count == 2

    service.update_user("alice", UserUpdate(device_limit=None))

    assert "x-hwid-limit" not in fetch(service, "device-00000003").headers
    assert service.user_devices("alice").count == 3
    assert "x-hwid-limit" in fetch(service, "device-00000004").headers


@pytest.mark.parametrize("suffix", ["", "json", "mihomo", "singbox"])
def test_a_new_device_at_the_cap_gets_unusable_placeholders_in_each_format(
    service: ControlService,
    tmp_path: Path,
    suffix: str,
) -> None:
    write_privacy(tmp_path / "control.db", PrivacySettings(device_limits="full", default_device_limit=1))
    fetch(service)
    response = fetch(service, "device-00000002", suffix=suffix)
    text = unquote(base64.b64decode(response.body).decode()) if not suffix else response.body

    assert response.status == 200 and "Working endpoint" not in text
    for remark in DEVICE_LIMIT_REMARKS:
        assert remark in text
    assert {
        name: response.headers[name] for name in ("x-hwid-active", "x-hwid-max-devices-reached", "x-hwid-limit")
    } == {
        "x-hwid-active": "true",
        "x-hwid-max-devices-reached": "true",
        "x-hwid-limit": "true",
    }
    assert len(device_rows(tmp_path)) == 1


@pytest.mark.parametrize(
    "hwid",
    [None, "", "short", "a" * 65, "unsupported/id"],
    ids=["absent", "empty", "short", "overlong", "unsupported-characters"],
)
def test_apps_without_a_supported_hwid_are_served_and_counted_nowhere(
    service: ControlService,
    tmp_path: Path,
    hwid: str | None,
) -> None:
    write_privacy(tmp_path / "control.db", PrivacySettings(device_limits="private"))
    response = fetch(service, hwid)
    assert "Working endpoint" in unquote(base64.b64decode(response.body).decode())
    assert "x-hwid-limit" not in response.headers and device_rows(tmp_path) == []


@pytest.mark.parametrize(
    ("agent", "accept", "suffix"),
    [
        ("Mozilla/5.0", "*/*", ""),
        ("Mozilla/5.0", "application/json", "json"),
        ("Happ/1.0", "text/html", "json"),
        ("Mozilla/5.0", "text/html", ""),
    ],
    ids=["page-fetch", "explicit-suffix", "html-accept", "navigation"],
)
def test_browsers_never_read_hwid_even_when_the_url_names_an_app_format(
    service: ControlService,
    tmp_path: Path,
    agent: str,
    accept: str,
    suffix: str,
) -> None:
    write_privacy(tmp_path / "control.db", PrivacySettings(device_limits="full"))
    user = service.get_user("alice")
    assert user is not None
    response = service.subscription(
        user.sub_token, suffix, user_agent=agent, accept=accept, device_headers=OnlyHwid(allowed=False)
    )
    assert response.status == 200 and device_rows(tmp_path) == []
    assert not any(name.startswith("x-hwid") for name in response.headers)


def test_full_seen_times_and_details_update_at_most_once_a_minute(tmp_path: Path) -> None:
    clock = [1800000000.0]
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0", clock=lambda: clock[0])
    try:
        user = service.put_user("alice")
        write_privacy(tmp_path / "control.db", PrivacySettings(device_limits="full"))
        headers = {"x-hwid": HWID, "x-device-model": "First model"}
        service.subscription(user.sub_token, device_headers=headers)
        first = service.user_devices("alice").devices[0].to_data()
        clock[0] += 59.9
        headers["x-device-model"] = "Updated model"
        service.subscription(user.sub_token, device_headers=headers)
        assert service.user_devices("alice").devices[0].to_data() == first
        clock[0] += 0.1
        service.subscription(user.sub_token, device_headers=headers)
        after = service.user_devices("alice").devices[0].to_data()
        assert after["first_seen_at"] == first["first_seen_at"]
        assert after["last_seen_at"] != first["last_seen_at"] and after["model"] == "Updated model"
        service.update_user("alice", UserUpdate(status="disabled"))
        clock[0] += 60
        service.subscription(user.sub_token, device_headers=headers)
        assert service.user_devices("alice").devices[0].to_data()["last_seen_at"] != after["last_seen_at"]
        service.subscription(user.sub_token, device_headers={"x-hwid": "device-00000002"})
        assert service.user_devices("alice").count == 1
    finally:
        service.close()


@pytest.mark.parametrize("level", ["private", "full"])
def test_parallel_new_fetches_cannot_overfill_a_cap(service: ControlService, tmp_path: Path, level: str) -> None:
    write_privacy(tmp_path / "control.db", PrivacySettings(device_limits=level, default_device_limit=3))  # type: ignore[arg-type]
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda index: fetch(service, f"device-{index:08d}"), range(8)))
    assert sum("x-hwid-limit" not in response.headers for response in responses) == 3
    assert service.user_devices("alice").count == 3


def test_caps_and_removals_belong_to_each_user_and_reissue_forgets_only_their_devices(
    service: ControlService,
    tmp_path: Path,
) -> None:
    write_privacy(tmp_path / "control.db", PrivacySettings(device_limits="full", default_device_limit=2))
    service.create_user(UserCreate(username="bob", device_limit=1))
    fetch(service)
    fetch(service, "device-00000002")
    fetch(service, username="bob")
    assert "x-hwid-limit" in fetch(service, "device-00000002", username="bob").headers
    assert service.delete_user_devices("alice", HWID).deleted == 1
    assert service.delete_user_devices("alice", HWID).deleted == 0
    assert service.user_devices("bob").count == 1
    assert service.delete_user_devices("alice").deleted == 1
    assert service.delete_user_devices("alice").deleted == 0
    before = service.get_user("bob")
    assert before is not None
    service.update_user("bob", UserUpdate(status="disabled"))
    after = service.reissue_user("bob")
    assert after.device_limit == 1 and after.status == "disabled"
    assert (after.uuid, after.sub_token) != (before.uuid, before.sub_token)
    assert service.user_devices("bob").count == 0 and service.subscription(before.sub_token).status == 404


def test_a_user_delete_cascades_devices_and_a_status_edit_keeps_the_cap(
    service: ControlService, tmp_path: Path
) -> None:
    write_privacy(tmp_path / "control.db", PrivacySettings(device_limits="private"))
    service.update_user("alice", UserUpdate(device_limit=2))
    fetch(service)
    assert service.update_user("alice", UserUpdate(status="disabled")).device_limit == 2
    assert service.put_user("alice").device_limit == 2
    fetch(service, "device-00000002")
    assert len(device_rows(tmp_path)) == 1
    service.delete_user("alice")
    assert device_rows(tmp_path) == []


@pytest.mark.parametrize("cap", [0, -1, 101], ids=["zero", "negative", "over-100"])
def test_invalid_caps_never_enter_user_models(cap: int) -> None:
    for model, extra in ((UserUpdate, {}), (UserCreate, {"username": "alice"})):
        with pytest.raises(ValidationError):
            model.model_validate({**extra, "device_limit": cap})


def test_privacy_downgrade_rekeys_and_drops_details_in_the_writers_transaction(
    service: ControlService,
    tmp_path: Path,
) -> None:
    path = tmp_path / "control.db"
    write_privacy(path, PrivacySettings(device_limits="full"))
    fetch(service)
    before = device_rows(tmp_path)
    store = Store(path)
    ledger = DeviceLedger(store)
    try:
        with pytest.raises(RuntimeError, match="roll back"), store.transaction():
            ledger.lower_level("full", "private")
            raise RuntimeError("roll back the privacy write")
        assert device_rows(tmp_path) == before and store.secret(DEVICE_HMAC_SECRET) is None
        with store.transaction():
            assert ledger.lower_level("full", "private") == 0
            store.db.execute(
                "UPDATE privacy SET settings = ? WHERE id = 1",
                (PrivacySettings(device_limits="private").model_dump_json(),),
            )
        (row,) = device_rows(tmp_path)
        assert row[1] != HWID and row[2:] == (None,) * 6
        assert "x-hwid-limit" not in fetch(service).headers and len(device_rows(tmp_path)) == 1
        with store.transaction():
            ledger.lower_level("full", "private")
        assert device_rows(tmp_path) == [row]
        with store.transaction():
            assert ledger.lower_level("private", "off") == 1
            assert ledger.lower_level("private", "off") == 0
        assert device_rows(tmp_path) == [] and store.secret(DEVICE_HMAC_SECRET) is None
    finally:
        store.close()


def test_raising_private_to_full_recognizes_the_existing_slot_before_learning_details(
    service: ControlService,
    tmp_path: Path,
) -> None:
    path = tmp_path / "control.db"
    write_privacy(path, PrivacySettings(device_limits="private", default_device_limit=1))
    fetch(service)
    fingerprint = service.user_devices("alice").devices[0].device_key
    write_privacy(path, PrivacySettings(device_limits="full", default_device_limit=1))
    assert "x-hwid-limit" not in fetch(service).headers
    view = service.user_devices("alice")
    assert view.count == 1 and view.devices[0].device_key == HWID != fingerprint
    assert view.devices[0].to_data()["first_seen_at"] is not None
