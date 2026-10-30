"""Privacy reviews, partial settings, daily client usage, and the public JSON command tree."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
import typer
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.client import run_show
from meridian.commands.client_usage import run_usage
from meridian.commands.fleet_usage import run_usage as run_fleet_usage
from meridian.commands.privacy import run_set
from meridian.commands.privacy import run_show as run_privacy_show
from meridian.console import set_json_mode
from meridian.control.client import ControlClient
from meridian.control.models import PrivacySettings
from meridian.core.schema import schema_model
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.topology import single_exit_intent


@contextmanager
def _fleet(tmp_path: Path, *, json_mode: bool = False) -> Iterator[ControlClient]:
    set_json_mode(json_mode)
    try:
        with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as client:
            cluster = ClusterConfig(
                panel=PanelConfig(url=url, api_token=ADMIN_TOKEN, server_ip="198.51.100.1"),
                topology_intent=single_exit_intent(users=["alice"]),
            )
            client.put_user("alice")
            with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
                yield client
    finally:
        set_json_mode(False)


def _envelope(capsys: pytest.CaptureFixture[str], schema: str) -> dict:
    body = json.loads(capsys.readouterr().out)
    schema_model(schema).model_validate(body)
    return body


def test_raising_privacy_prints_the_disclosure_before_confirmation_and_declining_changes_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _fleet(tmp_path) as client:

        def decline(*_args: object, **_kwargs: object) -> bool:
            output = capsys.readouterr().err
            assert "unreadable fingerprint" in output and "After a restore" in output
            assert client.privacy().settings == PrivacySettings()
            return False

        with patch("meridian.commands.privacy.confirm", side_effect=decline), pytest.raises(typer.Exit) as raised:
            run_set(devices="private")
        assert raised.value.exit_code == 1 and client.privacy().changes == []


def test_partial_privacy_flags_preserve_every_omitted_setting_and_an_identical_set_does_not_confirm(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _fleet(tmp_path, json_mode=True) as client:
        client.set_privacy(
            PrivacySettings(device_limits="full", default_device_limit=5, usage_history="daily", history_days=14)
        )
        run_set(history_days="7", yes=True)
        first = _envelope(capsys, "privacy-set-envelope")
        assert first["status"] == "changed"
        assert first["data"]["settings"] == {
            "device_limits": "full",
            "default_device_limit": 5,
            "usage_history": "daily",
            "history_days": 7,
        }
        with patch("meridian.commands.privacy.confirm", side_effect=AssertionError("a no-op must not confirm")):
            run_set(history_days="7")
        again = _envelope(capsys, "privacy-set-envelope")
        assert again["status"] == "no_changes" and again["summary"]["changed"] is False
        assert len(again["data"]["changes"]) == 2


def test_yes_skips_confirmation_and_keeps_the_full_and_daily_disclosures_visible_with_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _fleet(tmp_path, json_mode=True), patch("meridian.commands.privacy.confirm", side_effect=AssertionError):
        run_set(devices="full", history="daily", history_days="30", yes=True)
    output = capsys.readouterr()
    schema_model("privacy-set-envelope").model_validate_json(output.out)
    disclosure = " ".join(output.err.split())
    assert "which devices each person uses" in disclosure and "30 days" in disclosure
    assert "Addresses are never stored" in disclosure


def test_a_json_privacy_change_needs_yes_and_refuses_before_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with _fleet(tmp_path, json_mode=True) as client:
        with pytest.raises(typer.Exit) as raised:
            run_set(history="daily")
        assert raised.value.exit_code == 2 and client.privacy().changes == []
    body = _envelope(capsys, "privacy-set-envelope")
    assert "--yes" in body["errors"][0]["hint"]


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"devices": "raw"},
        {"history": "hourly"},
        {"device_limit": "0"},
        {"history_days": "731"},
        {"history_days": "7.5"},
        {"device_limit": "9" * 5000},
    ],
)
def test_bad_privacy_values_keep_a_json_error_envelope_and_never_open_a_connection(
    options: dict,
    capsys: pytest.CaptureFixture[str],
) -> None:
    set_json_mode(True)
    try:
        with patch("meridian.commands.privacy.access_panel") as access, pytest.raises(typer.Exit) as raised:
            run_set(**options, yes=True)
        assert raised.value.exit_code == 2
        access.assert_not_called()
        _envelope(capsys, "privacy-set-envelope")
    finally:
        set_json_mode(False)


def test_lowering_history_says_what_is_deleted_and_returns_the_two_row_counts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _fleet(tmp_path, json_mode=True) as client:
        client.set_privacy(PrivacySettings(usage_history="daily"))
        user = client.get_user("alice")
        with sqlite3.connect(tmp_path / "control.db") as db:
            db.execute("INSERT INTO traffic_daily VALUES (?, '2026-10-29', 10, 20)", (user.user_id,))
            db.execute("INSERT INTO node_traffic_daily VALUES ('main', '2026-10-29', 30)")
        run_set(history="off", yes=True)
    output = capsys.readouterr()
    assert "All daily history for people and servers will be deleted" in output.err
    body = json.loads(output.out)
    assert body["data"]["deleted"] == {"devices": 0, "traffic_daily": 1, "node_traffic_daily": 1}


def test_privacy_show_returns_the_change_log_and_explains_each_level(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with _fleet(tmp_path) as client:
        client.set_privacy(PrivacySettings(device_limits="private"))
        run_privacy_show()
        text = capsys.readouterr().err
        assert "unreadable fingerprint" in text and "ID, system, model" in text
        assert "Last privacy changes" in text and "root" in text
        set_json_mode(True)
        run_privacy_show()
    body = _envelope(capsys, "privacy-show-envelope")
    assert body["data"]["changes"][0]["after"]["device_limits"] == "private"


def test_client_usage_reads_console_people_and_client_show_adds_only_seven_days_when_on(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _fleet(tmp_path, json_mode=True) as client:
        client.put_user("console-person")
        with patch("meridian.commands.client.page_url", return_value=""):
            run_show("alice")
            assert _envelope(capsys, "client-show-envelope")["data"]["usage"] is None
            client.set_privacy(PrivacySettings(usage_history="daily", history_days=14))
            run_show("alice")
            assert _envelope(capsys, "client-show-envelope")["data"]["usage"]["days"] == 7
        run_usage("console-person", days="30")
        usage = _envelope(capsys, "client-usage-envelope")["data"]
        assert usage["username"] == "console-person" and usage["days"] == 14
        set_json_mode(False)
        run_usage("console-person", days="7")
    assert "Day (UTC)" in capsys.readouterr().err


@pytest.mark.parametrize("days", ["0", "731", "hourly", "9" * 5000])
def test_bad_usage_days_are_refused_before_service_access(days: str, capsys: pytest.CaptureFixture[str]) -> None:
    set_json_mode(True)
    try:
        with patch("meridian.commands.client_usage.access_panel") as access, pytest.raises(typer.Exit) as raised:
            run_usage("alice", days=days)
        assert raised.value.exit_code == 2
        access.assert_not_called()
        _envelope(capsys, "client-usage-envelope")
    finally:
        set_json_mode(False)


def test_history_off_is_a_user_error_with_the_opt_in_command_as_a_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with _fleet(tmp_path, json_mode=True), pytest.raises(typer.Exit) as raised:
        run_usage("alice")
    assert raised.value.exit_code == 2
    body = _envelope(capsys, "client-usage-envelope")
    assert "meridian privacy set --history daily" in body["errors"][0]["hint"]


def test_fleet_usage_reads_retained_node_totals_as_typed_json_and_a_compact_table(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _fleet(tmp_path, json_mode=True) as client:
        client.set_privacy(PrivacySettings(usage_history="daily", history_days=7))
        with sqlite3.connect(tmp_path / "control.db") as db:
            db.execute("INSERT INTO node_traffic_daily VALUES ('retired', date('now'), 42)")
        run_fleet_usage(days="30")
        body = _envelope(capsys, "fleet-usage-envelope")["data"]
        assert body["days"] == 7 and body["nodes"][0]["node_id"] == "retired"
        assert body["daily"][-1]["bytes"] == body["nodes"][0]["daily"][-1]["bytes"] == 42
        set_json_mode(False)
        run_fleet_usage(days="1")
    assert "retired" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["privacy", "--json"],
        ["--json", "privacy"],
        ["privacy", "show", "--json"],
        ["--json", "privacy", "show"],
        ["privacy", "--json", "show"],
    ],
)
def test_bare_privacy_is_show_and_every_declared_json_position_suppresses_the_banner(
    argv: list[str],
    tmp_path: Path,
    tmp_home: Path,
) -> None:
    with _fleet(tmp_path):
        result = CliRunner().invoke(app, argv)
    assert result.exit_code == 0, result.output
    schema_model("privacy-show-envelope").model_validate_json(result.stdout)
    assert "beta" not in result.stderr and "Meridian v" not in result.stderr
