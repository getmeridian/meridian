"""`meridian panel`: the web console's URL, and a one-time setup link only when one is due."""

from __future__ import annotations

import json
import re
import urllib.request

from typer.testing import CliRunner

import meridian.cli as cli
from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.control.client import ControlClient, ControlServiceNotFound, add_mutation_listener
from meridian.control.models import OwnerStatus
from tests.support.control_service import ADMIN_TOKEN, serve_control

runner = CliRunner()


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_panel_without_a_deployment_is_a_user_error(tmp_home, monkeypatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)

    result = runner.invoke(app, ["panel"])

    assert result.exit_code == 2
    assert "No panel is deployed yet" in _plain(result.output)


# -- a fleet on Meridian's own control service -------------------------------------------


def _save_service_fleet(monkeypatch, url: str) -> None:
    monkeypatch.setattr(ClusterConfig, "runs_control_service", property(lambda self: True))
    ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN)).save()


def _setup_token(output: str) -> str:
    match = re.search(r"#setup=([A-Za-z0-9_-]+)", output)
    assert match, output
    return match.group(1)


def _check_link(url: str, token: str) -> dict:
    request = urllib.request.Request(
        f"{url}/auth/setup/check",
        data=json.dumps({"token": token}).encode(),
        headers={"Content-Type": "application/json", "X-Meridian-Request": "1"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def test_panel_on_a_service_fleet_prints_the_console_and_a_one_time_setup_link(tmp_home, monkeypatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    with serve_control(tmp_home, public_path="/s3cret") as url:
        _save_service_fleet(monkeypatch, url)

        result = runner.invoke(app, ["panel"])

        output = _plain(result.output)
        assert result.exit_code == 0, output
        assert "Meridian web panel" in output
        assert f"{url}/" in output
        assert "Works once, for 15 minutes" in output
        assert "Remnawave" not in output
        assert _check_link(url, _setup_token(output))["purpose"] == "setup"


def test_bare_panel_asks_for_a_setup_link_never_a_reset_and_pulls_no_backup(tmp_home, monkeypatch) -> None:
    """A bare `panel` that raced an owner's setup would have wiped their password; minting a link backs nothing up."""
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    asked: list[bool] = []
    original = ControlClient.issue_setup_link

    def recording(self, *, reset: bool):  # type: ignore[no-untyped-def]
        asked.append(reset)
        return original(self, reset=reset)

    monkeypatch.setattr(ControlClient, "issue_setup_link", recording)
    reported: list[str] = []
    stop = add_mutation_listener(reported.append)
    try:
        with serve_control(tmp_home, public_path="/s3cret") as url:
            _save_service_fleet(monkeypatch, url)
            assert runner.invoke(app, ["panel"]).exit_code == 0
            assert runner.invoke(app, ["panel", "--setup-link"]).exit_code == 0
    finally:
        stop()
    assert asked == [False, True]
    assert reported == []


def test_panel_with_an_owner_prints_no_link_unless_asked(tmp_home, monkeypatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    monkeypatch.setattr(ControlClient, "owner_status", lambda self: OwnerStatus(exists=True, totp_enabled=True))
    with serve_control(tmp_home, public_path="/s3cret") as url:
        _save_service_fleet(monkeypatch, url)

        quiet = _plain(runner.invoke(app, ["panel"]).output)
        asked = runner.invoke(app, ["panel", "--setup-link"])

        assert "with a sign-in code" in quiet and "--setup-link" in quiet
        assert "#setup=" not in quiet
        assert asked.exit_code == 0
        assert _check_link(url, _setup_token(_plain(asked.output)))["purpose"] in ("setup", "reset")


def test_panel_on_a_service_without_the_console_asks_for_an_apply(tmp_home, monkeypatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)

    def older_service(self):
        raise ControlServiceNotFound("Not found.", category="user")

    monkeypatch.setattr(ControlClient, "owner_status", older_service)
    _save_service_fleet(monkeypatch, "https://198.51.100.10/0123abcd")

    result = runner.invoke(app, ["panel"])

    assert result.exit_code == 2
    assert "meridian apply" in _plain(result.output)


def test_panel_without_the_control_service_points_at_migrate(tmp_home, monkeypatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    ClusterConfig(panel=PanelConfig(url="https://198.51.100.10/0123abcd", admin_user="meridian-ab12")).save()

    result = runner.invoke(app, ["panel"])

    assert result.exit_code == 2
    assert "meridian migrate" in " ".join(_plain(result.output).split())
