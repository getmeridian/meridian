"""Schedule previews return shared dates without changing account or request state."""

import sqlite3
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from meridian.control.client import ControlClient
from meridian.control.models import UserCreate
from meridian.control.service import ControlService
from tests.support.control_service import ADMIN_TOKEN, serve_control


@pytest.fixture
def pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    now = "2027-01-31T12:00:00Z"
    monkeypatch.setattr("meridian.control.service.utc_now", lambda: now)
    monkeypatch.setattr(
        "tests.support.control_service.ControlService",
        lambda *args, **kwargs: ControlService(*args, **kwargs, clock=lambda: datetime.fromisoformat(now).timestamp()),
    )


def dump(path: Path) -> str:
    with sqlite3.connect(path) as db:
        return "\n".join(db.iterdump())


def test_preview_existing_account_dates_and_creation_duration_are_read_only(tmp_path: Path, pinned) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as admin:
        admin.create_user(UserCreate(username="example", expire_at="2027-02-15T12:00:00Z"))
        before = dump(tmp_path / "control.db")
        with httpx.Client(base_url=url, trust_env=False, headers={"Authorization": "Bearer " + ADMIN_TOKEN}) as raw:
            response = raw.post(
                "/api/users/preview",
                json={
                    "username": "example",
                    "traffic_limit_strategy": "month_rolling",
                    "traffic_reset_anchor_day": 31,
                    "renewal_interval_months": 1,
                    "renewal_limit": 2,
                },
            )
            assert response.status_code == 200
            preview = response.json()
            assert preview["expire_at"] == "2027-02-15T12:00:00Z"
            assert preview["next_reset_at"] == preview["next_renewal_at"] == "2027-02-15T12:00:00Z"
            assert preview["renewals_left"] == 2
            response = raw.post("/api/users/preview", json={"activation_duration_days": 30})
            assert response.status_code == 200
            assert response.json()["activation_pending"] and response.json()["expire_at"] is None
        assert dump(tmp_path / "control.db") == before


def test_preview_requires_authentication_and_people_scope(tmp_path: Path, pinned) -> None:
    with serve_control(tmp_path) as url, httpx.Client(base_url=url, trust_env=False) as raw:
        body = {"activation_duration_days": 30}
        assert raw.post("/api/users/preview", json=body).status_code == 401
        root = {"Authorization": "Bearer " + ADMIN_TOKEN}
        issued = raw.post("/api/tokens", json={"name": "preview-reader", "scope": "read"}, headers=root).json()
        assert (
            raw.post(
                "/api/users/preview", json=body, headers={"Authorization": "Bearer " + issued["token"]}
            ).status_code
            == 403
        )
        issued = raw.post("/api/tokens", json={"name": "preview-operator", "scope": "people"}, headers=root).json()
        assert (
            raw.post(
                "/api/users/preview", json=body, headers={"Authorization": "Bearer " + issued["token"]}
            ).status_code
            == 200
        )


@pytest.mark.parametrize(
    "body",
    [
        {"activation_duration_days": 0},
        {"activation_duration_days": "30"},
        {"traffic_reset_anchor_day": 32},
        {"renewal_interval_days": 7, "renewal_interval_months": 1},
        {"expire_at": "2027-02-15T00:00:00Z", "activation_duration_days": 30},
        {"renewal_interval_days": 7},
    ],
)
def test_invalid_schedule_previews_are_refused_without_writes(tmp_path: Path, pinned, body: dict) -> None:
    with serve_control(tmp_path) as url:
        before = dump(tmp_path / "control.db")
        with httpx.Client(base_url=url, trust_env=False, headers={"Authorization": "Bearer " + ADMIN_TOKEN}) as raw:
            response = raw.post("/api/users/preview", json=body)
        assert response.status_code == 400
        assert dump(tmp_path / "control.db") == before
