"""Legacy Remnawave compilation never silently accepts Meridian access profiles."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from meridian import i18n
from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.profile_review import review_profile_resources
from meridian.compiler import compile_topology
from meridian.compiler.errors import TopologyCompileError
from meridian.console import set_json_mode, set_quiet_mode
from meridian.core.topology import ControlPlaneIntent, SetupIntent
from meridian.i18n import t
from meridian.servers import ServerRegistry
from meridian.setup.fleet_document import fleet_document
from tests.support.control_service import ADMIN_TOKEN
from tests.support.topology import single_exit_intent


@pytest.fixture(autouse=True)
def command_modes(monkeypatch):
    monkeypatch.setattr(i18n, "_current", "en")
    monkeypatch.setattr(i18n, "_requested", None)
    yield
    set_json_mode(False)
    set_quiet_mode(False)


def legacy_intent() -> SetupIntent:
    return single_exit_intent(control=ControlPlaneIntent(server_ref="srv-control", backend="remnawave"))


@pytest.mark.parametrize("kind", ["named", "creation"])
def test_remnawave_compilation_refuses_profile_definitions_and_initial_membership(kind: str):
    data = legacy_intent().to_data()
    if kind == "named":
        data["access_profiles"] = [{"id": "limited", "grants": []}]
    else:
        data["access"]["creation_settings"] = {"default": {"profiles": ["limited"]}}
    with pytest.raises(TopologyCompileError, match="meridian migrate"):
        compile_topology(SetupIntent.model_validate(data))


def test_reviewing_resources_without_meridian_settings_never_raises_or_queries_a_service():
    intent = legacy_intent()
    plan = compile_topology(intent)
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.10/console", api_token=ADMIN_TOKEN), topology_intent=intent
    )
    with patch("meridian.commands.profile_review.ControlClient") as client:
        assert review_profile_resources(cluster, plan.resources) is None
    client.assert_not_called()


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize("command", ["add", "import", "apply", "plan"])
def test_legacy_profile_commands_fail_with_migration_guidance_before_runtime_or_service_access(
    tmp_path: Path, tmp_home: Path, locale: str, command: str
):
    before = legacy_intent()
    proposed = SetupIntent.model_validate({**before.to_data(), "access_profiles": [{"id": "limited", "grants": []}]})
    plan = compile_topology(before)
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.10/console", api_token=ADMIN_TOKEN),
        topology_intent=proposed if command in {"apply", "plan"} else before,
    )
    review = SimpleNamespace(plan=plan, review_hash="a" * 64, retiring=[])
    inspection = SimpleNamespace(
        plan_hash=plan.plan_hash,
        review_hash="a" * 64,
        converged=False,
        retiring=[],
        fence_warning="",
        inspections=[
            SimpleNamespace(action=SimpleNamespace(resource=row), error="", converged=True) for row in plan.resources
        ],
    )
    source = tmp_path / "fleet.yml"
    source.write_text(json.dumps(fleet_document(proposed, ServerRegistry(tmp_home / "servers.json"))))
    argv = {
        "add": ["profile", "add", "limited", "--empty", "--dry-run", "--json"],
        "import": ["fleet", "import", str(source), "--dry-run", "--json"],
        "apply": ["apply", "--yes", "--json"],
        "plan": ["plan", "--json"],
    }[command]
    with (
        patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.profiles.reviewed_runtime") as profiles,
        patch("meridian.commands.fleet_transfer.reviewed_runtime") as importer,
        patch("meridian.commands.v4_topology.reviewed_runtime") as apply,
        patch("meridian.setup.runtime.SetupRuntime") as planner,
        patch("meridian.commands.v4_topology.forget_unreachable_servers"),
        patch("meridian.commands.profile_review.ControlClient") as client,
    ):
        for runtime in (profiles, importer, apply):
            runtime.return_value.review.return_value = review
        planner.return_value.inspect_intent.return_value = inspection
        reply = CliRunner().invoke(app, ["--lang", locale, *argv])
        assert reply.exit_code == (1 if command == "plan" else 2), str(reply.exception)
        result = json.loads(reply.stdout)
        assert result["errors"][0]["category"] == "user"
        assert "meridian migrate" in result["errors"][0]["message"]
        assert result["errors"][0]["message"] == t("profiles.migrate_first")
        for runtime in (profiles, importer, apply, planner):
            runtime.assert_not_called()
        client.assert_not_called()


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize("command", ["single", "bulk"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_legacy_profile_assignment_refuses_before_directory_or_service_access(
    tmp_home: Path, locale: str, command: str, dry_run: bool
):
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.10/console", api_token=ADMIN_TOKEN), topology_intent=legacy_intent()
    )
    argv = (
        ["client", "set", "default", "--profile", "default"]
        if command == "single"
        else ["client", "bulk", "set-profiles", "--person", "default", "--profile", "default"]
    )
    with (
        patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
        patch(
            "meridian.commands.client_profiles.resolve_client_name", side_effect=AssertionError("Directory I/O ran.")
        ) as directory,
        patch("meridian.commands.client_profiles.access_panel", side_effect=AssertionError("Panel I/O ran.")) as panel,
        patch(
            "meridian.commands.client_profiles.ControlClient", side_effect=AssertionError("Service I/O ran.")
        ) as single,
        patch("meridian.commands.client_bulk.ControlClient", side_effect=AssertionError("Service I/O ran.")) as bulk,
    ):
        reply = CliRunner().invoke(
            app, ["--lang", locale, *argv, "--yes", "--json", *(["--dry-run"] if dry_run else [])]
        )
        assert reply.exit_code == 2, str(reply.exception)
        result = json.loads(reply.stdout)
        assert result["errors"][0]["category"] == "user"
        assert result["errors"][0]["message"] == t("profiles.migrate_first")
        for boundary in (directory, panel, single, bulk):
            boundary.assert_not_called()
