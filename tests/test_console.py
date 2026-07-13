"""Tests for console output helpers — confirm(), fail(), prompt()."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.console import confirm, fail, prompt, set_json_mode, set_quiet_mode


class TestFail:
    def test_raises_typer_exit(self) -> None:
        with pytest.raises(typer.Exit) as exc_info:
            fail("broken")
        assert exc_info.value.exit_code == 1

    def test_fail_user_exit_code_2(self) -> None:
        with pytest.raises(typer.Exit) as exc_info:
            fail("bad input", hint_type="user")
        assert exc_info.value.exit_code == 2

    def test_fail_system_exit_code_3(self) -> None:
        with pytest.raises(typer.Exit) as exc_info:
            fail("infra issue", hint_type="system")
        assert exc_info.value.exit_code == 3

    def test_fail_cancelled_exit_code_130(self) -> None:
        with pytest.raises(typer.Exit) as exc_info:
            fail("cancelled", hint_type="cancelled")
        assert exc_info.value.exit_code == 130

    def test_fail_explicit_exit_code_overrides(self) -> None:
        with pytest.raises(typer.Exit) as exc_info:
            fail("custom", hint_type="user", exit_code=42)
        assert exc_info.value.exit_code == 42

    def test_fail_with_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fail() with hint should include both message and hint in stderr."""
        with pytest.raises(typer.Exit):
            fail("deployment failed", hint="try meridian check")
        captured = capsys.readouterr()
        assert "deployment failed" in captured.err
        assert "try meridian check" in captured.err

    def test_fail_without_hint(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(typer.Exit):
            fail("just a message")
        captured = capsys.readouterr()
        assert "just a message" in captured.err
        # Should still include the report link
        assert "github.com" in captured.err

    def test_fail_includes_report_link(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(typer.Exit):
            fail("error occurred")
        captured = capsys.readouterr()
        assert "github.com/getmeridian/meridian/issues" in captured.err

    def test_fail_hint_type_user_no_github_link(self, capsys: pytest.CaptureFixture[str]) -> None:
        """hint_type='user' should not show GitHub link."""
        with pytest.raises(typer.Exit):
            fail("Invalid IP address", hint_type="user")
        captured = capsys.readouterr()
        assert "Invalid IP address" in captured.err
        assert "github.com" not in captured.err
        assert "diagnostics" not in captured.err

    def test_fail_hint_type_system_suggests_diagnostics(self, capsys: pytest.CaptureFixture[str]) -> None:
        """hint_type='system' should suggest meridian doctor."""
        with pytest.raises(typer.Exit):
            fail("SSH connection failed", hint_type="system")
        captured = capsys.readouterr()
        assert "SSH connection failed" in captured.err
        assert "doctor" in captured.err
        assert "github.com" not in captured.err

    def test_fail_hint_type_bug_is_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Default hint_type='bug' shows GitHub link."""
        with pytest.raises(typer.Exit):
            fail("unexpected state", hint_type="bug")
        captured = capsys.readouterr()
        assert "github.com/getmeridian/meridian/issues" in captured.err

    def test_fail_json_mode_emits_structured_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        set_json_mode(True)
        set_quiet_mode(True)
        try:
            with pytest.raises(typer.Exit) as exc_info:
                fail(
                    "Invalid token api_token=secret-token",
                    hint="Set password=hunter2 again",
                    hint_type="user",
                )
        finally:
            set_json_mode(False)
            set_quiet_mode(False)

        assert exc_info.value.exit_code == 2
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["schema"] == "meridian.output/v1"
        assert data["command"] == "cli.error"
        assert data["status"] == "failed"
        assert data["exit_code"] == 2
        assert data["errors"][0]["code"] == "MERIDIAN_USER_ERROR"
        assert data["errors"][0]["category"] == "user"
        assert "secret-token" not in captured.out
        assert "hunter2" not in captured.out

    def test_fail_json_mode_emits_cancelled_status(self, capsys: pytest.CaptureFixture[str]) -> None:
        set_json_mode(True)
        set_quiet_mode(True)
        try:
            with pytest.raises(typer.Exit) as exc_info:
                fail("Cancelled by user", hint_type="cancelled")
        finally:
            set_json_mode(False)
            set_quiet_mode(False)

        assert exc_info.value.exit_code == 130
        data = json.loads(capsys.readouterr().out)
        assert data["status"] == "cancelled"
        assert data["errors"][0]["category"] == "cancelled"


def _make_tty_mock(input_text: str) -> MagicMock:
    """Create a context manager mock that simulates /dev/tty with given input."""
    tty_io = StringIO(input_text + "\n")
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=tty_io)
    mock_cm.__exit__ = MagicMock(return_value=False)
    return mock_cm


class TestConfirm:
    def test_confirm_y(self) -> None:
        with patch("builtins.open", return_value=_make_tty_mock("y")):
            assert confirm("Deploy?") is True

    def test_confirm_Y(self) -> None:
        with patch("builtins.open", return_value=_make_tty_mock("Y")):
            assert confirm("Deploy?") is True

    def test_confirm_yes(self) -> None:
        with patch("builtins.open", return_value=_make_tty_mock("yes")):
            assert confirm("Deploy?") is True

    def test_confirm_empty_enter_defaults_yes(self) -> None:
        with patch("builtins.open", return_value=_make_tty_mock("")):
            assert confirm("Deploy?") is True

    def test_confirm_n_returns_false(self) -> None:
        with patch("builtins.open", return_value=_make_tty_mock("n")):
            assert confirm("Deploy?") is False

    def test_confirm_N_returns_false(self) -> None:
        with patch("builtins.open", return_value=_make_tty_mock("N")):
            assert confirm("Deploy?") is False

    def test_confirm_no_tty_returns_false(self) -> None:
        """When /dev/tty is not available (CI), default to reject."""
        with patch("builtins.open", side_effect=OSError("No TTY")):
            assert confirm("Deploy?") is False


class TestPrompt:
    def test_prompt_returns_input(self) -> None:
        with patch("builtins.open", return_value=_make_tty_mock("198.51.100.10")):
            result = prompt("IP address")
        assert result == "198.51.100.10"

    def test_prompt_empty_returns_default(self) -> None:
        with patch("builtins.open", return_value=_make_tty_mock("")):
            result = prompt("IP address", default="198.51.100.11")
        assert result == "198.51.100.11"

    def test_prompt_no_tty_returns_default(self) -> None:
        with patch("builtins.open", side_effect=OSError("No TTY")):
            result = prompt("IP address", default="fallback")
        assert result == "fallback"

    def test_prompt_no_tty_no_default(self) -> None:
        with patch("builtins.open", side_effect=OSError("No TTY")):
            result = prompt("IP address")
        assert result == ""

    def test_prompt_strips_whitespace(self) -> None:
        with patch("builtins.open", return_value=_make_tty_mock("  hello  ")):
            result = prompt("Name")
        assert result == "hello"
