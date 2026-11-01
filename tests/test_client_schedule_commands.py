"""CLI schedule edits preview the service's dates and preserve pending activation."""

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.client import run_show
from meridian.commands.client_limits import format_usage, run_set
from meridian.console import set_json_mode
from meridian.control.client import ControlClient
from meridian.control.service import ControlService
from meridian.core.clients import build_client_record
from meridian.remnawave import User
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.topology import single_exit_intent


@pytest.fixture
def fleet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ControlClient]:
    now = "2027-01-31T12:00:00Z"
    monkeypatch.setattr("meridian.control.service.utc_now", lambda: now)
    monkeypatch.setattr(
        "tests.support.control_service.ControlService",
        lambda *args, **kwargs: ControlService(*args, **kwargs, clock=lambda: datetime.fromisoformat(now).timestamp()),
    )
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as client:
        client.put_user("example")
        cluster = ClusterConfig(
            panel=PanelConfig(url=url, api_token=ADMIN_TOKEN, server_ip="198.51.100.1"),
            topology_intent=single_exit_intent(users=["example"]),
        )
        set_json_mode(True)
        try:
            with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
                yield client
        finally:
            set_json_mode(False)


def test_set_and_show_carry_the_full_schedule_and_wait_for_traffic(fleet, capsys) -> None:
    run_set(
        "example",
        limit=None,
        reset="month_rolling",
        expires=None,
        duration="30",
        reset_day="31",
        renew_days="7",
        renewals="2",
        yes=True,
    )
    client = json.loads(capsys.readouterr().out)["data"]["client"]
    assert client["activation_duration_days"] == 30 and client["traffic_reset_anchor_day"] == 31
    assert client["renewal_interval_days"] == 7 and client["renewal_limit"] == 2
    assert client["schedule"]["activation_pending"] and client["expires_at"] == ""
    run_show("example")
    shown = json.loads(capsys.readouterr().out)["data"]["client"]
    assert shown["schedule"]["renewals_left"] == 2
    assert fleet.get_user("example").first_traffic_at is None


def test_dry_run_returns_a_schedule_preview_and_does_not_save(fleet, capsys) -> None:
    before = fleet.get_user("example")
    run_set("example", limit=None, reset="month_rolling", expires=None, duration="30", dry_run=True)
    result = json.loads(capsys.readouterr().out)
    assert not result["summary"]["changed"] and not result["data"]["applied"]
    assert result["data"]["schedule_preview"]["activation_pending"]
    assert fleet.get_user("example") == before


def test_json_schedule_change_needs_yes_and_does_not_save_on_refusal(fleet, capsys) -> None:
    with pytest.raises(typer.Exit) as refused:
        run_set("example", limit=None, reset=None, expires=None, duration="30")
    assert refused.value.exit_code == 2
    assert json.loads(capsys.readouterr().out)["errors"]
    assert fleet.get_user("example").activation_duration_days is None


def test_anniversary_usage_text_does_not_claim_an_edited_anchor_is_the_creation_day() -> None:
    person = User(
        traffic_limit_strategy="MONTH_ROLLING", traffic_reset_anchor_day=31, created_at="2027-01-15T12:00:00Z"
    )
    assert "resets monthly on the chosen UTC day" in format_usage(build_client_record(person))
