"""Device management over HTTP: scope, header extraction, and opaque removal IDs."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx
import pytest

from meridian.control.client import ControlClient, ControlServiceNotFound, ControlServiceScopeError
from meridian.control.models import PrivacySettings, UserUpdate
from tests.support.control_service import ADMIN_TOKEN, serve_control, write_privacy


@pytest.mark.parametrize("level", ["private", "full"])
def test_device_reads_need_read_and_removals_need_admin_even_for_the_same_user(tmp_path: Path, level: str) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as admin:
        user = admin.put_user("a.b")
        write_privacy(tmp_path / "control.db", PrivacySettings(device_limits=level, default_device_limit=1))  # type: ignore[arg-type]
        reader = admin.create_token("reader", "read")
        headers = {
            "X-Hwid": "ABCdef1234==",
            "X-Device-OS": "Example OS",
            "X-Ver-OS": "1.0",
            "X-Device-Model": "Example device",
            "User-Agent": "v2RayTun/1.0",
            "X-Real-IP": "198.51.100.20",
        }
        with httpx.Client(base_url=url, trust_env=False) as app:
            assert app.get(f"/sub/{user.sub_token}", headers=headers).status_code == 200
            assert (
                app.get("/api/health", headers={**headers, "Authorization": "Bearer " + ADMIN_TOKEN}).status_code == 200
            )
        with ControlClient(url, reader.token) as client:
            view = client.user_devices("a.b")
            assert (view.level, view.count, view.limit) == (level, 1, 1)
            device = view.devices[0].to_data()
            if level == "private":
                assert list(device) == ["device_key"] and device["device_key"] != headers["X-Hwid"]
            else:
                assert device["device_key"] == headers["X-Hwid"] and device["platform"] == "Example OS"
                assert device["os_version"] == "1.0" and device["model"] == "Example device"
            with pytest.raises(ControlServiceScopeError):
                client.delete_user_devices("a.b", device["device_key"])
        assert admin.user_devices("a.b").count == 1
        assert admin.delete_user_devices("a.b", device["device_key"]).deleted == 1
        assert admin.delete_user_devices("a.b", device["device_key"]).deleted == 0
        with pytest.raises(ControlServiceNotFound):
            admin.user_devices("missing")
    with sqlite3.connect(tmp_path / "control.db") as db:
        assert db.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 0


def test_repeated_hwid_headers_use_the_first_value_and_a_request_off_sub_counts_nowhere(tmp_path: Path) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as admin:
        user = admin.put_user("alice")
        write_privacy(tmp_path / "control.db", PrivacySettings(device_limits="full"))
        with httpx.Client(base_url=url, trust_env=False) as app:
            headers = [("X-Hwid", "device-00000001"), ("x-hwid", "device-00000002")]
            assert (
                app.get("/api/users/alice", headers=[*headers, ("Authorization", "Bearer " + ADMIN_TOKEN)]).status_code
                == 200
            )
            assert admin.user_devices("alice").count == 0
            assert app.get(f"/sub/{user.sub_token}/json", headers=headers).status_code == 200
        assert [device.device_key for device in admin.user_devices("alice").devices] == ["device-00000001"]


def test_the_connection_pages_account_check_registers_no_device(tmp_path: Path) -> None:
    """The page's `/info` fetch is a browser reading status, never an app taking a device slot."""
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as admin:
        user = admin.put_user("alice")
        write_privacy(tmp_path / "control.db", PrivacySettings(device_limits="full"))
        with httpx.Client(base_url=url, trust_env=False) as app:
            answer = app.get(f"/sub/{user.sub_token}/info", headers={"X-Hwid": "device-00000001"})
            assert answer.status_code == 200 and answer.json()["isFound"] is True
        assert admin.user_devices("alice").count == 0


def test_a_partial_http_cap_edit_keeps_other_limits_and_an_explicit_null_uses_the_default(tmp_path: Path) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as admin:
        admin.put_user("alice")
        write_privacy(tmp_path / "control.db", PrivacySettings(default_device_limit=7))
        first = admin.update_user("alice", UserUpdate(device_limit=2, traffic_limit_bytes=5000))
        assert first.device_limit == 2
        assert admin.put_user("alice").device_limit == 2
        after = admin.update_user("alice", UserUpdate(device_limit=None))
        assert after.device_limit is None and after.traffic_limit_bytes == 5000
        assert admin.user_devices("alice").limit == 7
        with httpx.Client(base_url=url, trust_env=False, headers={"Authorization": "Bearer " + ADMIN_TOKEN}) as raw:
            assert raw.patch("/api/users/alice", json={"device_limit": 0}).status_code == 200
        assert admin.user_devices("alice").limit == 0
