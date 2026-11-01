"""An explicit unlimited device cap must never inherit the fleet's cap."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from meridian.control.models import PrivacySettings, UserUpdate
from meridian.control.service import ControlService
from meridian.core.client_limits import build_limit_change
from meridian.migrate.users import carry_users
from meridian.remnawave import User
from tests.support.control_service import FLEET_ID, write_privacy


@pytest.mark.parametrize("value", ["unlimited", "0"])
def test_cli_device_exemption_is_zero_and_default_stays_null(value: str) -> None:
    assert build_limit_change(devices=value).device_limit == 0
    assert build_limit_change(devices="default").device_limit is None


@pytest.mark.parametrize("level", ["private", "full"])
def test_unlimited_person_accepts_devices_above_the_fleet_cap_and_inherit_restores_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, level: str
) -> None:
    moment = "2026-10-30T12:00:00Z"
    clock = datetime.fromisoformat(moment).astimezone(UTC).timestamp()
    monkeypatch.setattr("meridian.control.service.utc_now", lambda: moment)
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0", clock=lambda: clock)
    try:
        person = service.put_user("example")
        write_privacy(tmp_path / "control.db", PrivacySettings(device_limits=level, default_device_limit=1))
        service.update_user("example", UserUpdate(device_limit=0))
        for index in range(3):
            response = service.subscription(
                person.sub_token, user_agent="ExampleApp/1", device_headers={"x-hwid": f"example-device-{index}"}
            )
            assert "x-hwid-limit" not in response.headers
        assert (service.user_devices("example").limit, service.user_devices("example").count) == (0, 3)
        service.update_user("example", UserUpdate(device_limit=None))
        response = service.subscription(
            person.sub_token, user_agent="ExampleApp/1", device_headers={"x-hwid": "example-device-3"}
        )
        assert "x-hwid-limit" in response.headers
        assert service.user_devices("example").limit == 1
    finally:
        service.close()


@pytest.mark.parametrize("cap", [0, 7, None])
def test_migration_preserves_the_device_cap_without_importing_devices(cap: int | None) -> None:
    person = User(
        username="example",
        vless_uuid="3f2a9c4e-7b1d-4e6a-9c2f-1a2b3c4d5e6f",
        short_uuid="exampleToken0001",
        status="ACTIVE",
        traffic_limit_strategy="NO_RESET",
        hwid_device_limit=cap,
    )
    result = carry_users([person], ["example"])
    assert result.batch.users[0].device_limit == cap
    assert result.notes == {}
