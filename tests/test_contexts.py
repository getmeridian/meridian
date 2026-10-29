"""Fleet contexts: selection precedence, adoption of an existing home, isolation, and `meridian context`."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from meridian import config
from meridian.cli import app
from meridian.cluster import ClusterConfig
from meridian.console import set_json_mode
from meridian.contexts import ContextError, context_home, resolve_context, save_context
from meridian.core.errors import LocalStateError
from meridian.core.schema import schema_model
from meridian.servers import ServerEntry, ServerRegistry
from tests.conftest import CONTEXT_BOUND
from tests.support.topology import single_exit_intent


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An unpinned install home, as on a user's machine: no MERIDIAN_HOME, contexts available."""
    home = tmp_path / "meridian"
    home.mkdir()
    monkeypatch.delenv("MERIDIAN_HOME", raising=False)
    monkeypatch.delenv("MERIDIAN_CONTEXT", raising=False)
    monkeypatch.setattr(config, "PINNED_HOME", False)
    monkeypatch.setattr(config, "INSTALL_HOME", home)
    monkeypatch.setattr(config, "CACHE_DIR", home / "cache")
    monkeypatch.setattr(config, "PREFERENCES_FILE", home / "preferences.json")
    monkeypatch.setattr(config, "MERIDIAN_SSH_KEY_FILE", home / "ssh" / "meridian_ed25519")
    for name in CONTEXT_BOUND:
        monkeypatch.setattr(config, name, getattr(config, name))
    config.select_context()
    yield home
    set_json_mode(False)


def _fleet(home: Path, *, control: str, exit_host: str = "") -> None:
    """Write a deployed fleet's local files: a control server that is also the exit, plus an optional second exit."""
    registry = ServerRegistry(home / "servers.json")
    registry.add(ServerEntry(host=control, name="control", id="srv-control"))
    intent = single_exit_intent(control="srv-control", exit_ref="srv-control")
    if exit_host:
        registry.add(ServerEntry(host=exit_host, name="second", id="srv-second"))
        second = intent.exits[0].model_copy(update={"id": "exit-b", "server_ref": "srv-second"})
        intent = intent.model_copy(update={"exits": [*intent.exits, second]})
    ClusterConfig(topology_intent=intent).save(home / "cluster.yml")


def _cli(*args: str, env: dict[str, str] | None = None) -> Any:
    return CliRunner().invoke(app, list(args), env=env)


PRECEDENCE = [
    # pinned, flag, env, saved, expected name, expected source
    (True, "", "work", "family", "default", "pinned"),
    (False, "work", "family", "office", "work", "flag"),
    (False, "", "family", "office", "family", "env"),
    (False, "", "", "office", "office", "saved"),
    (False, "", "", "", "default", "default"),
]


@pytest.mark.parametrize(
    ("pinned", "flag", "env", "saved", "name", "source"),
    PRECEDENCE,
    ids=["meridian-home-pins", "flag-beats-env", "env-beats-saved", "saved", "nothing-means-default"],
)
def test_selection_precedence(
    tmp_path: Path, pinned: bool, flag: str, env: str, saved: str, name: str, source: str
) -> None:
    if saved:
        save_context(tmp_path, saved)
    selection = resolve_context(tmp_path, pinned=pinned, flag="" if pinned else flag, env=env)
    assert (selection.name, selection.source) == (name, source)
    assert selection.home == (tmp_path if name == "default" else tmp_path / "contexts" / name)


def test_a_flag_beside_meridian_home_is_refused_not_ignored(tmp_path: Path) -> None:
    with pytest.raises(ContextError, match="MERIDIAN_HOME"):
        resolve_context(tmp_path, pinned=True, flag="work")


@pytest.mark.parametrize("name", ["Work", "../x", "", "-lead", "a" * 33, "with space"])
def test_malformed_names_are_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(ContextError):
        resolve_context(tmp_path, pinned=False, flag=name or "Bad Name")


def test_an_existing_home_becomes_default_without_moving_a_file(install: Path) -> None:
    _fleet(install, control="198.51.100.10")
    before = {path: path.read_bytes() for path in install.rglob("*") if path.is_file()}

    result = _cli("context", "list", "--json")

    assert result.exit_code == 0, result.output
    assert config.CLUSTER_CONFIG == install / "cluster.yml"
    assert config.SERVER_PROFILES_FILE == install / "servers.json"
    assert {path: path.read_bytes() for path in install.rglob("*") if path.is_file()} == before
    [default] = json.loads(result.stdout)["data"]["contexts"]
    assert default["name"] == "default" and default["current"] and default["control"] == "198.51.100.10"


def test_list_reports_each_fleet_from_local_files(install: Path) -> None:
    _fleet(install, control="198.51.100.10", exit_host="198.51.100.20")
    _fleet(context_home(install, "family"), control="198.51.100.40")
    (install / "contexts" / "broken").mkdir()
    (install / "contexts" / "broken" / "cluster.yml").write_text(": not yaml [")

    result = _cli("context", "list", "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    schema_model("context-list-envelope").model_validate(payload)
    rows = {row["name"]: row for row in payload["data"]["contexts"]}
    assert list(rows) == ["default", "broken", "family"]
    assert (rows["default"]["control"], rows["default"]["exits"], rows["default"]["current"]) == (
        "198.51.100.10",
        2,
        True,
    )
    assert (rows["family"]["control"], rows["family"]["exits"], rows["family"]["state"]) == ("198.51.100.40", 1, "ok")
    assert rows["broken"]["state"] == "unreadable"


def test_human_list_marks_the_current_context(install: Path) -> None:
    _fleet(install, control="198.51.100.10", exit_host="198.51.100.20")
    _fleet(context_home(install, "family"), control="198.51.100.40")
    save_context(install, "family")

    result = _cli("context", "list")

    assert result.exit_code == 0, result.output
    assert "  default  198.51.100.10    2 exits" in result.output
    assert "* family   198.51.100.40    1 exit" in result.output


def test_edits_in_one_context_never_touch_another(install: Path) -> None:
    _fleet(install, control="198.51.100.10")
    default_files = {path: path.read_bytes() for path in install.glob("*.json")} | {
        install / "cluster.yml": (install / "cluster.yml").read_bytes()
    }

    config.select_context("work")
    ServerRegistry(config.SERVER_PROFILES_FILE).add(ServerEntry(host="203.0.113.5", name="work-exit"))
    ClusterConfig(topology_intent=single_exit_intent()).save()

    work = install / "contexts" / "work"
    assert (work / "servers.json").is_file() and (work / "cluster.yml").is_file()
    assert {path: path.read_bytes() for path in default_files} == default_files
    assert config.MERIDIAN_SSH_KEY_FILE == install / "ssh" / "meridian_ed25519"


def test_use_persists_and_commands_follow_it(install: Path) -> None:
    _fleet(context_home(install, "family"), control="198.51.100.40")

    assert _cli("context", "use", "family").exit_code == 0
    assert (install / "current-context").read_text().strip() == "family"
    config.select_context()
    assert config.CLUSTER_CONFIG == install / "contexts" / "family" / "cluster.yml"

    assert _cli("context", "use", "default").exit_code == 0
    assert not (install / "current-context").exists()


@pytest.mark.parametrize(
    "argv",
    [["--context", "nope", "client", "list"], ["client", "list", "--context", "nope"]],
    ids=["before-the-command", "after-the-command"],
)
def test_a_missing_context_stops_fleet_commands_with_candidates(install: Path, argv: list[str]) -> None:
    (install / "contexts" / "family").mkdir(parents=True)

    result = _cli(*argv)

    assert result.exit_code == 2
    assert "No context named 'nope'" in result.output
    assert "default, family" in result.output


def test_a_missing_context_is_a_json_error_when_json_follows_the_command(install: Path) -> None:
    result = _cli("client", "list", "--context", "nope", "--json")

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed" and "No context named 'nope'" in payload["errors"][0]["message"]


def test_conflicting_context_flags_are_refused(install: Path) -> None:
    result = _cli("--context", "a", "client", "list", "--context", "b")
    assert result.exit_code == 2
    assert "Two contexts given" in result.output


def test_deploy_may_start_a_new_context(install: Path) -> None:
    from meridian.commands.context import select_for_invocation

    select_for_invocation(root="", leaf="work", command="deploy")

    assert config.CONTEXT == "work"
    assert config.SERVER_PROFILES_FILE == install / "contexts" / "work" / "servers.json"


def test_rename_moves_the_directory_and_follows_the_current_context(install: Path) -> None:
    _fleet(context_home(install, "work"), control="198.51.100.40")
    save_context(install, "work")

    result = _cli("context", "rename", "work", "office")

    assert result.exit_code == 0, result.output
    assert (install / "contexts" / "office" / "cluster.yml").is_file()
    assert not (install / "contexts" / "work").exists()
    assert (install / "current-context").read_text().strip() == "office"


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["context", "use", "nope"], "No context named 'nope'"),
        (["context", "rename", "default", "x"], "default context keeps its name"),
        (["context", "rename", "family", "default"], "default context keeps its name"),
        (["context", "rename", "family", "office"], "already exists"),
        (["context", "rename", "family", "Bad"], "Invalid context name"),
        (["context", "remove", "default"], "default context cannot be removed"),
        (["context", "remove", "office"], "still has servers"),
    ],
    ids=["use-missing", "rename-default", "rename-to-default", "rename-onto-existing", "rename-bad", "remove-default",
         "remove-fleet-needs-yes"],
)  # fmt: skip
def test_context_refusals(install: Path, argv: list[str], message: str) -> None:
    (install / "contexts" / "family").mkdir(parents=True)
    _fleet(context_home(install, "office"), control="198.51.100.40")

    result = _cli(*argv)

    assert result.exit_code == 2, result.output
    assert message in result.output
    assert (install / "contexts" / "family").is_dir() and (install / "contexts" / "office").is_dir()


@pytest.mark.parametrize(
    ("current", "env"),
    [("family", {}), ("default", {"MERIDIAN_CONTEXT": "family"})],
    ids=["saved", "from-environment"],
)
def test_remove_refuses_the_current_context(install: Path, current: str, env: dict[str, str]) -> None:
    (install / "contexts" / "family").mkdir(parents=True)
    save_context(install, current)

    result = _cli("context", "remove", "family", env=env)

    assert result.exit_code == 2
    assert "current context" in result.output
    assert (install / "contexts" / "family").is_dir()


def test_remove_deletes_only_local_files_once_confirmed(install: Path) -> None:
    _fleet(context_home(install, "office"), control="198.51.100.40")

    result = _cli("context", "remove", "office", "--yes")

    assert result.exit_code == 0, result.output
    assert not (install / "contexts" / "office").exists()


def test_remove_declined_keeps_the_fleet(install: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fleet(context_home(install, "office"), control="198.51.100.40")
    monkeypatch.setattr("meridian.commands.context.has_tty", lambda: True)
    monkeypatch.setattr("meridian.commands.context.confirm", lambda *_a, **_k: False)

    result = _cli("context", "remove", "office")

    assert result.exit_code == 1
    assert (install / "contexts" / "office" / "cluster.yml").is_file()


def test_remove_of_an_empty_context_needs_no_confirmation(install: Path) -> None:
    (install / "contexts" / "spare").mkdir(parents=True)
    assert _cli("context", "remove", "spare").exit_code == 0
    assert not (install / "contexts" / "spare").exists()


@pytest.mark.parametrize("argv", [["context", "use", "family"], ["context", "remove", "family", "--yes"]])
def test_context_edits_are_refused_while_meridian_home_pins_one_fleet(tmp_home: Path, argv: list[str]) -> None:
    result = _cli(*argv)
    assert result.exit_code == 2
    assert "MERIDIAN_HOME" in result.output


def test_a_server_serves_one_fleet_at_a_time(install: Path) -> None:
    from meridian.fleet_contexts import refuse_servers_claimed_elsewhere

    _fleet(context_home(install, "family"), control="198.51.100.40")
    config.select_context("work")

    with pytest.raises(LocalStateError, match="context 'family'"):
        refuse_servers_claimed_elsewhere(["203.0.113.5", "198.51.100.40"])
    refuse_servers_claimed_elsewhere(["203.0.113.5"])
    config.select_context("family")
    refuse_servers_claimed_elsewhere(["198.51.100.40"])  # its own fleet


def test_every_public_leaf_accepts_context_except_the_context_group() -> None:
    from meridian.cli_catalog import long_options, public_commands

    for path, command in public_commands().items():
        assert ("--context" in long_options(command)) != path.startswith("context."), path


def test_studio_deploys_run_on_the_context_studio_serves(install: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from meridian.adapters import deploy_process
    from meridian.core.deploy import DeployRequest

    seen: list[list[str]] = []

    class _Stop(Exception):
        pass

    def popen(argv: list[str], **_kwargs: Any) -> None:
        seen.append(argv)
        raise _Stop

    monkeypatch.setattr(subprocess, "Popen", popen)
    config.select_context("work")
    with pytest.raises(_Stop):
        deploy_process.run_deploy_process(DeployRequest(ip="198.51.100.10"), operation=_Operation())

    assert seen[0][-2:] == ["--context", "work"]


class _Operation:
    def on_cancel(self, _callback: Any) -> None:
        return None
