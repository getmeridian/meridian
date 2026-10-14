"""CLI entry points turn library errors into user-facing failures, never tracebacks."""

from __future__ import annotations

import json
import re

from typer.testing import CliRunner

import meridian.cli as cli
from meridian.cli import app
from meridian.console import set_json_mode, set_quiet_mode

runner = CliRunner()


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _reset_output_modes() -> None:
    set_json_mode(False)
    set_quiet_mode(False)


class TestEntryPointErrors:
    def test_setup_with_unreadable_intent_is_a_user_error_not_a_traceback(self, monkeypatch, tmp_home) -> None:
        monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)

        result = runner.invoke(app, ["setup", "--intent", str(tmp_home / "missing.json"), "--yes"])

        output = _strip_ansi(result.output)
        assert result.exit_code == 2
        assert "Cannot read setup intent" in output
        assert "Traceback" not in output

    def test_check_json_validation_error_names_the_check_command(self, monkeypatch, tmp_home) -> None:
        monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
        _reset_output_modes()
        try:
            result = runner.invoke(app, ["check", "--domain", "bad_domain", "--json"])
        finally:
            _reset_output_modes()

        assert result.exit_code == 2
        assert json.loads(result.output)["command"] == "check"
