"""Subscriber slot counts are read-only and reveal none of the device ledger."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from meridian.control.models import PrivacySettings, UserUpdate
from meridian.control.service import ControlService
from tests.support.control_service import FLEET_ID

NOW = "2026-11-01T12:00:00Z"


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ControlService]:
    monkeypatch.setattr("meridian.control.service.utc_now", lambda: NOW)
    service = ControlService(
        tmp_path / "control.db",
        fleet_id=FLEET_ID,
        version="4.0.0",
        clock=lambda: datetime.fromisoformat(NOW).timestamp(),
    )
    yield service
    service.close()


def info(service: ControlService) -> dict:
    user = service.get_user("sample")
    assert user is not None
    before = service._db.total_changes
    response = service.subscription(
        user.sub_token, "info", user_agent="Happ/1", device_headers={"x-hwid": "info-device-0001"}
    )
    assert service._db.total_changes == before
    return json.loads(response.body)


@pytest.mark.parametrize("level", ["private", "full"])
def test_info_exposes_only_used_and_allowed_slots_without_registering_the_browser(
    service: ControlService, level: str
) -> None:
    user = service.put_user("sample")
    service.set_privacy(PrivacySettings.model_validate({"device_limits": level}), author="owner")
    assert info(service)["user"]["deviceSlots"] == {"used": 0, "allowed": 3}
    headers = {"x-hwid": "device-sample-0001", "x-device-model": "Private model", "x-device-os": "Private platform"}
    service.subscription(user.sub_token, device_headers=headers)
    result = info(service)
    assert result["user"]["deviceSlots"] == {"used": 1, "allowed": 3}
    assert service.user_devices("sample").count == 1
    device = service.user_devices("sample").devices[0]
    text = json.dumps(result)
    assert device.device_key not in text
    assert not any(value in text for value in ("Private model", "Private platform", "first_seen_at", "last_seen_at"))
    assert service.subscription(user.sub_token).headers.get("deviceSlots") is None


def test_slot_counts_follow_person_caps_and_removals_even_when_a_lowered_cap_is_full(service: ControlService) -> None:
    user = service.put_user("sample")
    service.set_privacy(PrivacySettings(device_limits="private"), author="owner")
    for index in range(3):
        service.subscription(user.sub_token, device_headers={"x-hwid": f"device-sample-{index:04d}"})
    service.update_user("sample", UserUpdate(device_limit=2))
    assert info(service)["user"]["deviceSlots"] == {"used": 3, "allowed": 2}
    service.delete_user_devices("sample")
    assert info(service)["user"]["deviceSlots"] == {"used": 0, "allowed": 2}


def test_info_omits_slots_when_device_limits_are_off_even_if_an_old_row_remains(service: ControlService) -> None:
    user = service.put_user("sample")
    service._db.execute("INSERT INTO devices (user_id, device_key) VALUES (?, ?)", (user.user_id, "device-sample-0001"))
    assert "deviceSlots" not in info(service)["user"]
    assert service._store.secret("device_hmac_key") is None
