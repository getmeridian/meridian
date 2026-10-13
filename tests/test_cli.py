"""Smoke tests for CLI commands using CliRunner."""

from __future__ import annotations

import json
import re

import pytest
import typer
from typer.testing import CliRunner

import meridian.cli as cli
from meridian.cli import app
from meridian.console import is_json_mode, set_json_mode, set_quiet_mode

runner = CliRunner()


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _reset_output_modes() -> None:
    set_json_mode(False)
    set_quiet_mode(False)


@pytest.fixture(autouse=True)
def _quiet_cli(monkeypatch):
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    _reset_output_modes()
    yield
    _reset_output_modes()


class TestCLIBasics:
    def test_version_flag(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "meridian" in _strip_ansi(result.output)

    def test_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        output = _strip_ansi(result.output)
        assert "setup" in output
        assert "deploy" in output
        assert "studio" in output
        assert "client" in output
        assert "server" in output

    def test_no_args_shows_help(self) -> None:
        result = runner.invoke(app)
        output = _strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Usage" in output and "deploy" in output

    def test_bare_meridian_in_a_terminal_without_a_fleet_offers_the_wizard(self, monkeypatch, tmp_home) -> None:
        started: list[bool] = []
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stderr.isatty", lambda: True)
        monkeypatch.setattr("meridian.commands.wizard_prompts.prompt", lambda label, default="": default)
        monkeypatch.setattr("meridian.commands.setup_v4.run", lambda **_kwargs: started.append(True))
        ctx = typer.Context(typer.main.get_command(app))

        cli._offer_setup_or_help(ctx, interactive=True)

        assert started == [True]

    def test_unsupported_lang_is_a_user_error(self) -> None:
        result = runner.invoke(app, ["--lang", "de", "server", "list"])
        assert result.exit_code == 2
        assert "Unsupported language: de" in _strip_ansi(result.output)


class TestHelpSections:
    def test_top_level_help_groups_commands_into_ordered_sections(self) -> None:
        result = runner.invoke(app, ["--help"])
        output = _strip_ansi(result.output)

        titles = ["Get started", "Clients", "Servers", "Check & fix", "Advanced"]
        positions = [output.index(f"─ {title} ") for title in titles]
        assert positions == sorted(positions)

        def section(title: str) -> str:
            start = output.index(f"─ {title} ")
            nxt = output.find("╭─", start)
            return output[start : nxt if nxt != -1 else len(output)]

        assert "setup" in section("Get started") and "deploy" in section("Get started")
        assert "client" in section("Clients")
        for name in ("node", "relay", "route", "fleet", "server"):
            assert name in section("Servers")
        for name in ("check", "test", "doctor", "plan", "apply"):
            assert name in section("Check & fix")
        assert "teardown" in section("Advanced")

    def test_every_public_command_has_a_help_section(self) -> None:
        group = typer.main.get_command(app)
        public = {name for name, command in group.commands.items() if not command.hidden}  # type: ignore[attr-defined]

        assert public == set(cli._HELP_ORDER)

    def test_hidden_dev_app_stays_out_of_top_level_help(self) -> None:
        output = _strip_ansi(runner.invoke(app, ["--help"]).output)

        assert not re.search(r"│ dev\s", output)
        assert re.search(r"│ probe\s", output)

    def test_deploy_help_groups_options_into_panels(self) -> None:
        output = _strip_ansi(runner.invoke(app, ["deploy", "--help"]).output)

        for panel in ("Server access", "Methods", "Branding", "Automation"):
            assert f"─ {panel} " in output
        methods = output[output.index("─ Methods ") :]
        assert "--hysteria2" in methods.split("╰")[0]
        assert "--dry-run" in output[output.index("─ Automation ") :].split("╰")[0]
        assert "--cancel-file" not in output

    def test_deploy_display_name_help_has_no_stray_backslash(self) -> None:
        output = _strip_ansi(runner.invoke(app, ["deploy", "--help"]).output)

        assert "\\'" not in output


class TestRenamedSpellings:
    def test_every_old_command_spelling_fails_with_a_pointer_to_the_new_one(self) -> None:

        for old, new in cli.RENAMED.items():
            result = runner.invoke(app, [*old.split(), "198.51.100.10"])

            assert result.exit_code == 2, old
            assert f"meridian {old}` is now `meridian {new}`" in _strip_ansi(result.output)

    def test_old_spellings_are_not_commands_any_more(self) -> None:
        group = typer.main.get_command(app)
        for old in cli.RENAMED:
            command: object = group
            for name in old.split():
                command = command.commands.get(name) if isinstance(command, typer.core.TyperGroup) else None  # type: ignore[attr-defined]
            assert command is None, old

    def test_server_option_points_at_the_positional_target(self) -> None:

        result = runner.invoke(app, ["doctor", "--server", "mybox"])

        assert result.exit_code == 2
        assert "`--server` is now the first argument: `meridian doctor SERVER`" in _strip_ansi(result.output)

    def test_renamed_command_in_json_mode_is_an_error_envelope(self) -> None:

        result = runner.invoke(app, ["relay", "check", "relay-a", "--json"])

        assert result.exit_code == 2
        payload = json.loads(result.output)
        assert payload["command"] == "relay.check"
        assert payload["status"] == "failed"
        assert payload["errors"][0]["category"] == "user"
        assert "meridian check" in payload["errors"][0]["message"]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["setup"], ["--intent", "--restart"]),
        (["deploy"], ["--domain", "--sni", "--json", "--events", "--request", "--dry-run"]),
        (["studio"], ["--port", "--assets-dir", "--no-open"]),
        (["client"], ["add", "list", "remove"]),
        (["server"], ["add", "list", "remove"]),
        (["fleet"], ["status", "inventory", "upgrade"]),
        (["fleet", "status"], ["--json"]),
        (["api"], ["commands", "schemas", "schema"]),
        (["doctor"], ["--ai"]),
        (["update"], []),
    ],
    ids=" ".join,
)
def test_subcommand_help(argv: list[str], expected: list[str]) -> None:
    result = runner.invoke(app, [*argv, "--help"])
    assert result.exit_code == 0
    output = _strip_ansi(result.output)
    for needle in expected:
        assert needle in output


@pytest.mark.parametrize("argv", [["studio", "--port", "65536"], ["apply", "--parallel", "0"]], ids=" ".join)
def test_out_of_range_option_values_are_user_errors(argv: list[str]) -> None:
    assert runner.invoke(app, argv).exit_code == 2


@pytest.mark.parametrize(
    ("argv", "target"),
    [
        (["plan", "--json"], "meridian.commands.plan.run"),
        (["--json", "apply", "--yes"], "meridian.commands.apply.run"),
        (["client", "list", "--json"], "meridian.commands.client.run_list"),
        (["client", "show", "alice", "--json"], "meridian.commands.client.run_show"),
        (["fleet", "status", "--json"], "meridian.commands.fleet.run_status"),
        (["fleet", "inventory", "--json"], "meridian.commands.fleet.run_inventory"),
    ],
    ids=lambda value: " ".join(value) if isinstance(value, list) else "",
)
def test_json_turns_on_json_mode_and_suppresses_the_banner(monkeypatch, argv: list[str], target: str) -> None:
    seen: list[bool] = []

    def fake_run(*_args: object, **_kwargs: object) -> None:
        seen.append(is_json_mode())
        print('{"ok": true}')

    monkeypatch.setattr(target, fake_run)

    result = runner.invoke(app, argv)

    assert result.exit_code == 0
    assert seen == [True]
    assert "Meridian v" not in result.output
    assert json.loads(result.output) == {"ok": True}


class TestCommandDispatch:
    def test_setup_forwards_resumable_options(self, monkeypatch) -> None:
        called: dict[str, object] = {}

        def fake_run(**kwargs: object) -> None:
            called.update(kwargs)

        monkeypatch.setattr("meridian.commands.setup_v4.run", fake_run)

        result = runner.invoke(
            app,
            [
                "setup",
                "--intent",
                "intent.json",
                "--restart",
                "--yes",
            ],
        )

        assert result.exit_code == 0
        assert called == {
            "intent_path": "intent.json",
            "restart": True,
            "yes": True,
        }

    def test_bare_deploy_asks_for_a_server_and_gives_up_without_one(self) -> None:

        result = runner.invoke(app, ["deploy"], input="\n\n\n")

        assert result.exit_code == 2
        assert "No server IP given" in _strip_ansi(result.output)

    def test_studio_command_delegates_to_runner(self, monkeypatch) -> None:
        called: dict[str, object] = {}

        def fake_run(*, port: int, assets_dir: str, no_open: bool) -> None:
            called.update({"port": port, "assets_dir": assets_dir, "no_open": no_open})

        monkeypatch.setattr("meridian.commands.studio.run", fake_run)

        result = runner.invoke(app, ["studio", "--port", "9876", "--assets-dir", "website/dist", "--no-open"])

        assert result.exit_code == 0
        assert called == {"port": 9876, "assets_dir": "website/dist", "no_open": True}

    def test_deploy_accepts_command_json_and_events(self, monkeypatch) -> None:
        called: dict[str, object] = {}

        def fake_run(*_args: object, **kwargs: object) -> None:
            called.update(kwargs)
            called["json"] = is_json_mode()

        monkeypatch.setattr("meridian.commands.deploy.run", fake_run)

        result = runner.invoke(
            app,
            ["deploy", "198.51.100.10", "--yes", "--json", "--events=jsonl", "--request", "deploy.json", "--dry-run"],
        )

        assert result.exit_code == 0
        assert called["json"] is True
        assert called["json_output"] is True
        assert called["events"] == "jsonl"
        assert called["request_path"] == "deploy.json"
        assert called["dry_run"] is True

    def test_deploy_accepts_global_json(self, monkeypatch) -> None:
        called: dict[str, object] = {}

        def fake_run(*_args: object, **kwargs: object) -> None:
            called.update(kwargs)
            called["json"] = is_json_mode()

        monkeypatch.setattr("meridian.commands.deploy.run", fake_run)

        result = runner.invoke(app, ["--json", "deploy", "198.51.100.10", "--yes"])

        assert result.exit_code == 0
        assert called["json"] is True
        assert called["json_output"] is True

    def test_update_propagates_system_failure_exit(self, monkeypatch) -> None:
        monkeypatch.setattr("meridian.update.run_self_update", lambda major=False: 3)

        result = runner.invoke(app, ["update"])

        assert result.exit_code == 3


class TestApiContractCLI:
    def test_api_schemas_json_is_parseable_machine_output(self) -> None:

        result = runner.invoke(app, ["api", "schemas", "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["command"] == "api.schemas"
        assert payload["data"]["schemas"]
        assert "Meridian v" not in result.output

    def test_include_schemas_implies_json_envelope(self) -> None:

        result = runner.invoke(app, ["api", "schemas", "--include-schemas"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["command"] == "api.schemas"
        assert payload["data"]["schemas"][0]["schema"]

    def test_api_commands_json_lists_command_contracts(self) -> None:

        result = runner.invoke(app, ["api", "commands", "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["command"] == "api.commands"
        commands = {item["command"]: item for item in payload["data"]["commands"]}
        assert commands["plan"]["envelope_schema"] == "plan-envelope"
        assert commands["fleet.status"]["data_schema"] == "fleet-status"
        assert commands["client.list"]["argv"] == ["client", "list"]
        assert commands["api.workflow"]["data_schema"] == "api-workflow"
        assert commands["deploy"]["data_schema"] == "deploy-command-data"
        assert {"--json", "--events", "--request", "--dry-run", "--cancel-file"} <= set(
            commands["deploy"]["machine_flags"]
        )

    def test_api_workflow_json_returns_deploy_fields(self) -> None:

        result = runner.invoke(app, ["api", "workflow", "deploy", "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["command"] == "api.workflow"
        assert payload["data"]["name"] == "deploy"
        workflow = payload["data"]["workflow"]
        assert workflow["id"] == "deploy"
        assert workflow["ready_request_schema"] == "deploy-request"
        assert [field["id"] for field in workflow["fields"]][:2] == ["ip", "user"]

    @pytest.mark.parametrize("argv", [["api", "schema", "missing", "--envelope"], ["api", "schema", "missing"]])
    def test_api_schema_error_is_command_scoped_json(self, argv: list[str]) -> None:
        result = runner.invoke(app, argv)
        assert result.exit_code == 2
        payload = json.loads(result.output)
        assert payload["command"] == "api.schema"
        assert payload["status"] == "failed"
        assert payload["errors"][0]["category"] == "user"

    def test_api_schema_json_alias_wraps_envelope(self) -> None:

        result = runner.invoke(app, ["api", "schema", "compiled-plan-result", "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["command"] == "api.schema"
        assert payload["status"] == "ok"
        assert payload["data"]["name"] == "compiled-plan-result"
        assert payload["data"]["schema"]["title"] == "CompiledPlanResult"

    def test_api_schema_raw_schema_preserves_sensitive_property_names(self) -> None:

        result = runner.invoke(app, ["api", "schema", "client-show"])

        assert result.exit_code == 0
        payload = json.loads(result.output)
        client_props = payload["$defs"]["ClientDetail"]["properties"]
        handoff_props = payload["$defs"]["ClientHandoff"]["properties"]
        assert "subscription_url" not in client_props
        assert "share_url" not in client_props
        assert handoff_props["subscription_available"]["type"] == "boolean"
        assert handoff_props["share_available"]["type"] == "boolean"


class TestCommandLocalJsonQuieting:
    def test_global_json_rejects_unmigrated_command(self) -> None:

        result = runner.invoke(app, ["--json", "node", "remove"])

        assert result.exit_code == 2
        payload = json.loads(result.output)
        assert payload["command"] == "node.remove"
        assert payload["status"] == "failed"
        assert payload["errors"][0]["category"] == "user"
        assert "Meridian v" not in result.output

    def test_global_json_is_accepted_exactly_by_commands_that_declare_json(self) -> None:
        from meridian.cli_catalog import public_commands, supports_json

        for path, command in public_commands().items():
            # --help stops after the root callback has accepted or refused the global --json.
            result = runner.invoke(app, ["--json", *path.split("."), "--help"])

            assert result.exit_code == (0 if supports_json(command) else 2), path

    def test_global_json_reaches_check_and_route_list(self, monkeypatch) -> None:
        seen: list[bool] = []
        monkeypatch.setattr("meridian.commands.health.run", lambda *_a, **_k: seen.append(is_json_mode()))
        monkeypatch.setattr("meridian.commands.route.run_list", lambda *_a, **_k: seen.append(is_json_mode()))

        for argv in (["--json", "check"], ["--json", "route", "list", "exit-1"]):
            result = runner.invoke(app, argv)
            assert result.exit_code == 0, argv

        assert seen == [True, True]

    def test_an_option_value_spelled_like_json_is_not_a_machine_flag(self, monkeypatch) -> None:
        seen: list[bool] = []
        monkeypatch.setattr("meridian.update.check_for_update", lambda _version: None)
        monkeypatch.setattr("meridian.commands.diagnostics.run", lambda *_a, **_k: seen.append(is_json_mode()))

        result = runner.invoke(app, ["doctor", "--sni", "--json"])

        assert result.exit_code == 0
        assert seen == [False]
        assert "Meridian v" in result.output


def test_beta_banner_points_testers_at_the_report_form(capsys) -> None:
    from meridian.console import banner

    banner("4.0.0b1")
    beta = capsys.readouterr().err
    banner("4.0.0")
    stable = capsys.readouterr().err

    assert "beta" in beta and "template=v4_beta.yml" in beta
    assert "beta" not in stable
