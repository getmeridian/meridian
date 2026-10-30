"""Client devices and caps: typed output, removals already done, and early validation."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import typer

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.client_devices import run_devices
from meridian.commands.client_limits import run_set
from meridian.console import set_json_mode
from meridian.control.client import ControlClient
from meridian.control.models import PrivacySettings
from meridian.core.client_limits import ClientLimitError, build_limit_change
from meridian.core.schema import schema_model
from tests.support.control_service import ADMIN_TOKEN, serve_control, write_privacy
from tests.support.topology import single_exit_intent


@pytest.fixture
def fleet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ControlClient]:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as client:
        client.put_user("alice")
        cluster = ClusterConfig(
            panel=PanelConfig(url=url, api_token=ADMIN_TOKEN, server_ip="198.51.100.10"),
            topology_intent=single_exit_intent(users=["alice"]),
        )
        monkeypatch.setattr("meridian.commands._helpers.ClusterConfig.load", lambda: cluster)
        set_json_mode(True)
        try:
            yield client
        finally:
            set_json_mode(False)


def envelope(capsys: pytest.CaptureFixture[str], schema: str) -> dict:
    result = json.loads(capsys.readouterr().out)
    schema_model(schema).model_validate(result)
    return result


def register(client: ControlClient, username: str, hwid: str) -> None:
    user = client.get_user(username)
    assert user is not None
    with httpx.Client(base_url=client.url, trust_env=False) as app:
        assert (
            app.get(f"/sub/{user.sub_token}", headers={"x-hwid": hwid, "x-device-os": "Example OS"}).status_code == 200
        )


def test_client_set_changes_only_the_cap_and_default_is_an_explicit_null(
    fleet: ControlClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_set("alice", limit=None, reset=None, expires=None, devices="2")
    assert envelope(capsys, "client-set-envelope")["data"]["client"]["device_limit"] == 2
    run_set("alice", limit="50GB", reset=None, expires=None)
    assert envelope(capsys, "client-set-envelope")["data"]["client"]["device_limit"] == 2
    run_set("alice", limit=None, reset=None, expires=None, devices="default")
    assert envelope(capsys, "client-set-envelope")["data"]["client"]["device_limit"] is None


@pytest.mark.parametrize(
    ("typed", "limit"),
    [("1", 1), ("100", 100), ("۲", 2), ("default", None)],
    ids=["minimum", "maximum", "persian-digit", "fleet-default"],
)
def test_device_cap_input_keeps_the_difference_between_omitted_and_default(typed: str, limit: int | None) -> None:
    change = build_limit_change(devices=typed)
    assert change.sets_devices and not change.is_empty and change.device_limit == limit
    assert not build_limit_change().sets_devices and build_limit_change().is_empty


@pytest.mark.parametrize(
    "typed",
    ["0", "101", "-1", "1.5", "none", "9" * 400],
    ids=["zero", "over-100", "negative", "fraction", "not-default", "enormous"],
)
def test_a_bad_device_cap_returns_a_user_error_before_opening_access(
    fleet: ControlClient,
    capsys: pytest.CaptureFixture[str],
    typed: str,
) -> None:
    with patch("meridian.commands.client_limits.access_panel") as access, pytest.raises(typer.Exit) as refused:
        run_set("alice", limit=None, reset=None, expires=None, devices=typed)
    assert refused.value.exit_code == 2
    assert envelope(capsys, "client-set-envelope")["errors"][0]["category"] == "user"
    access.assert_not_called()
    with pytest.raises(ClientLimitError):
        build_limit_change(devices=typed)


@pytest.mark.parametrize("level", ["private", "full"])
def test_devices_json_removal_and_clear_report_both_changes_and_already_forgotten_ids(
    fleet: ControlClient,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    level: str,
) -> None:
    write_privacy(tmp_path / "control.db", PrivacySettings(device_limits=level))  # type: ignore[arg-type]
    register(fleet, "alice", "device-00000001")
    register(fleet, "alice", "device-00000002")
    run_devices("alice")
    first = envelope(capsys, "client-devices-envelope")
    assert (first["data"]["count"], first["data"]["limit"], first["summary"]["changed"]) == (2, 3, False)
    if level == "private":
        assert all(list(device) == ["device_key"] for device in first["data"]["devices"])
    else:
        assert first["data"]["devices"][0]["platform"] == "Example OS"
    key = first["data"]["devices"][0]["device_key"]
    for removed in (1, 0):
        run_devices("alice", remove=key)
        result = envelope(capsys, "client-devices-envelope")
        assert result["data"]["removed"] == removed and result["summary"]["changed"] is bool(removed)
        assert result["data"]["count"] == 1
    for removed in (1, 0):
        run_devices("alice", clear=True)
        result = envelope(capsys, "client-devices-envelope")
        assert result["data"]["removed"] == removed and result["data"]["count"] == 0


def test_private_human_output_shows_a_count_without_fingerprints(
    fleet: ControlClient,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_privacy(tmp_path / "control.db", PrivacySettings(device_limits="private"))
    register(fleet, "alice", "device-00000001")
    fingerprint = fleet.user_devices("alice").devices[0].device_key
    set_json_mode(False)
    run_devices("alice")
    text = capsys.readouterr().err
    assert "1 of 3 devices" in text and fingerprint not in text and "device-00000001" not in text


def test_console_people_can_have_their_devices_removed_and_cleared_from_the_cli(
    fleet: ControlClient,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_privacy(tmp_path / "control.db", PrivacySettings(device_limits="full"))
    fleet.put_user("a.b")
    register(fleet, "a.b", "device-00000001")
    run_devices("a.b")
    assert envelope(capsys, "client-devices-envelope")["data"]["count"] == 1
    run_devices("a.b", remove="device-00000001")
    assert envelope(capsys, "client-devices-envelope")["data"]["removed"] == 1
    register(fleet, "a.b", "device-00000002")
    run_devices("a.b", clear=True)
    result = envelope(capsys, "client-devices-envelope")
    assert result["data"]["removed"] == 1 and fleet.user_devices("a.b").count == 0


@pytest.mark.parametrize(
    "options",
    [{"remove": "device-00000001", "clear": True}, {"remove": "unsafe/id"}],
    ids=["conflicting-actions", "invalid-id"],
)
def test_invalid_removal_options_fail_before_a_control_connection(
    fleet: ControlClient,
    options: dict,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch("meridian.commands.client_devices.access_panel") as access, pytest.raises(typer.Exit) as refused:
        run_devices("alice", **options)
    assert refused.value.exit_code == 2 and envelope(capsys, "client-devices-envelope")["status"] == "failed"
    access.assert_not_called()
