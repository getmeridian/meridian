"""A fleet still on Remnawave is named on load, and every command but the move points at `meridian migrate`."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from meridian import config
from meridian.cli import app
from meridian.cluster import ClusterConfig
from meridian.cluster_persistence import saved_backend
from meridian.console import set_json_mode, set_quiet_mode
from meridian.setup.single_server import single_server_intent

runner = CliRunner()


def _said(output: str) -> str:
    return " ".join(output.split())


@pytest.fixture(autouse=True)
def _human_output() -> Iterator[None]:
    yield
    set_json_mode(False)
    set_quiet_mode(False)


def _write(version: int, control: dict[str, object]) -> None:
    intent = single_server_intent(None, server_ref="srv-a").model_dump(mode="json")
    intent["control"] = {**intent["control"], **control}
    config.CLUSTER_CONFIG.write_text(yaml.safe_dump({"version": version, "topology_intent": intent}))


@pytest.mark.parametrize(
    ("version", "control", "backend"),
    [
        (3, {}, "remnawave"),
        (3, {"backend": "meridian"}, "meridian"),
        (4, {}, "meridian"),
        (4, {"backend": "remnawave"}, "remnawave"),
    ],
    ids=["v3-default-was-remnawave", "v3-hidden-meridian", "v4-default-is-meridian", "v4-named-remnawave"],
)
def test_a_missing_backend_means_what_the_files_version_defaulted_to(
    tmp_home: Path, version: int, control: dict[str, object], backend: str
) -> None:
    _write(version, control)

    assert saved_backend() == backend
    loaded = ClusterConfig.load()
    assert loaded.topology_intent is not None and loaded.topology_intent.control.backend == backend


def test_a_remnawave_fleet_saved_again_keeps_its_backend_named(tmp_home: Path) -> None:
    _write(3, {})

    ClusterConfig.load().save()

    saved = yaml.safe_load(config.CLUSTER_CONFIG.read_text())
    assert saved["version"] == 4 and saved["topology_intent"]["control"]["backend"] == "remnawave"


def test_commands_on_a_remnawave_fleet_point_at_migrate(tmp_home: Path) -> None:
    _write(3, {})

    human = runner.invoke(app, ["client", "list"])
    machine = runner.invoke(app, ["client", "list", "--json"])

    assert human.exit_code == 2 and "meridian migrate" in _said(human.output)
    envelope = json.loads(machine.stdout)
    assert machine.exit_code == 2 and envelope["status"] == "failed"


def test_migrate_runs_on_a_remnawave_fleet(tmp_home: Path) -> None:
    _write(3, {})

    with patch("meridian.commands.migrate.run") as migrate:
        result = runner.invoke(app, ["migrate", "--yes"])

    assert result.exit_code == 0, result.output
    migrate.assert_called_once_with(True)


def test_deploy_without_a_terminal_refuses_with_the_migrate_pointer(tmp_home: Path) -> None:
    _write(3, {})

    with patch("meridian.commands.migrate.run") as migrate:
        result = runner.invoke(app, ["deploy", "198.51.100.10", "--yes"])

    assert result.exit_code == 2 and "meridian migrate" in _said(result.output)
    migrate.assert_not_called()


def test_deploy_in_a_terminal_offers_the_move(tmp_home: Path) -> None:
    _write(3, {})

    with (
        patch("meridian.commands.deploy.has_tty", return_value=True),
        patch("meridian.commands.migrate.run") as migrate,
    ):
        result = runner.invoke(app, ["deploy", "198.51.100.10"])

    assert result.exit_code == 0, result.output
    migrate.assert_called_once_with()
    assert "meridian deploy" in _said(result.output)
