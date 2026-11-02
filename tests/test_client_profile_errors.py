"""Person profile edits report typed directory failures and one revocation warning."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from meridian import i18n
from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.console import set_json_mode, set_quiet_mode
from meridian.control.models import ServiceSettings
from meridian.core.errors import MeridianError
from meridian.i18n import t
from meridian.remnawave import RemnawaveNetworkError
from tests.support.control_service import ADMIN_TOKEN
from tests.support.token_service import serve_tokens
from tests.support.topology import panel_mock, single_exit_intent
from tests.test_access_profiles import POLICY, spec


@pytest.fixture(autouse=True)
def command_modes(monkeypatch):
    monkeypatch.setattr(i18n, "_current", "en")
    monkeypatch.setattr(i18n, "_requested", None)
    yield
    set_json_mode(False)
    set_quiet_mode(False)


@pytest.mark.parametrize("phase", ["open", "get-user"])
@pytest.mark.parametrize(
    "error",
    [
        MeridianError("The saved service address is missing.", category="user"),
        RemnawaveNetworkError("The control service did not answer.", category="system", retryable=True),
    ],
)
def test_profile_edit_converts_directory_errors_to_one_typed_cli_failure(
    tmp_home: Path, phase: str, error: MeridianError
):
    cluster = ClusterConfig(topology_intent=single_exit_intent())
    panel = panel_mock(list_users=[SimpleNamespace(username="default")])
    panel.get_user.side_effect = error
    with (
        patch("meridian.commands.client_profiles.load_topology_cluster", return_value=cluster),
        patch(
            "meridian.commands.client_profiles.access_panel",
            side_effect=error if phase == "open" else None,
            return_value=panel,
        ),
        patch("meridian.commands.client_profiles.ControlClient") as client,
    ):
        reply = CliRunner().invoke(app, ["client", "set", "default", "--profile", "limited", "--dry-run", "--json"])
    assert reply.exit_code == error.exit_code, str(reply.exception)
    result = json.loads(reply.stdout)
    assert result["command"] == "client.set" and result["status"] == "failed"
    assert len(result["errors"]) == 1
    assert result["errors"][0]["message"] == str(error)
    assert result["errors"][0]["category"] == error.category
    client.assert_not_called()


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_person_profile_loss_review_prints_the_revocation_warning_once(tmp_path: Path, tmp_home: Path, locale: str):
    with serve_tokens(tmp_path) as fleet:
        fleet.service.put_node_spec("main", spec(profiles=True))
        fleet.service.put_settings(ServiceSettings(profile_title="Meridian", access_profiles=POLICY))
        fleet.service.put_user("default")
        cluster = ClusterConfig(
            panel=PanelConfig(url=fleet.url, api_token=ADMIN_TOKEN), topology_intent=single_exit_intent()
        )
        warnings = []
        with (
            patch("meridian.commands.client_profiles.load_topology_cluster", return_value=cluster),
            patch("meridian.commands.client_profiles.warn", side_effect=warnings.append),
            patch("meridian.commands.client_bulk.warn", side_effect=warnings.append),
        ):
            reply = CliRunner().invoke(
                app, ["--lang", locale, "client", "set", "default", "--profile", "limited", "--dry-run"]
            )
        assert reply.exit_code == 0, reply.output
        assert warnings.count(t("profiles.revocation")) == 1
        assert fleet.service.get_user("default").profiles == ["default"]
