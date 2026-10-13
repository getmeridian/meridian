"""Tests for console output helpers — confirm(), fail(), prompt()."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.console import confirm, fail, info, prompt, set_json_mode, set_quiet_mode, warn

_REPORT_LINK = "github.com/getmeridian/meridian/issues"


@pytest.mark.parametrize(
    ("kwargs", "exit_code"),
    [
        ({}, 1),
        ({"hint_type": "user"}, 2),
        ({"hint_type": "system"}, 3),
        # A cooperative cancel is its own outcome, never the interrupt code.
        ({"hint_type": "cancelled"}, 5),
        ({"hint_type": "user", "exit_code": 42}, 42),
    ],
)
def test_fail_exit_code_follows_hint_type(kwargs: dict, exit_code: int) -> None:
    with pytest.raises(typer.Exit) as exc_info:
        fail("broken", **kwargs)
    assert exc_info.value.exit_code == exit_code


@pytest.mark.parametrize(
    ("hint_type", "present", "absent"),
    [
        ("bug", [_REPORT_LINK], []),
        ("user", [], ["github.com", "diagnostics"]),
        ("system", ["doctor"], ["github.com"]),
    ],
)
def test_fail_footer_follows_hint_type(capsys, hint_type: str, present: list[str], absent: list[str]) -> None:
    with pytest.raises(typer.Exit):
        fail("deployment failed", hint="try meridian check", hint_type=hint_type)
    err = capsys.readouterr().err
    assert "deployment failed" in err
    assert "try meridian check" in err
    for needle in present:
        assert needle in err
    for needle in absent:
        assert needle not in err


def test_fail_defaults_to_the_bug_report_footer(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit):
        fail("just a message")
    err = capsys.readouterr().err
    assert "just a message" in err
    assert _REPORT_LINK in err


def test_fail_renders_message_and_hint_as_plain_text(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit):
        fail("[red]message[/red]", hint="[bold]hint[/bold]", hint_type="user")

    captured = capsys.readouterr().err
    assert "[red]message[/red]" in captured
    assert "[bold]hint[/bold]" in captured


@pytest.fixture
def json_mode():
    set_json_mode(True)
    set_quiet_mode(True)
    yield
    set_json_mode(False)
    set_quiet_mode(False)


def test_fail_json_mode_emits_structured_redacted_error(json_mode, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit) as exc_info:
        fail("Invalid token api_token=secret-token", hint="Set password=hunter2 again", hint_type="user")

    assert exc_info.value.exit_code == 2
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["schema"] == "meridian.output/v1"
    assert data["command"] == "cli.error"
    assert data["status"] == "failed"
    assert data["exit_code"] == 2
    assert data["errors"][0]["code"] == "MERIDIAN_USER_ERROR"
    assert data["errors"][0]["category"] == "user"
    assert "secret-token" not in out
    assert "hunter2" not in out


def test_fail_json_mode_emits_cancelled_status(json_mode, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit) as exc_info:
        fail("Cancelled by user", hint_type="cancelled")

    assert exc_info.value.exit_code == 5
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "cancelled"
    assert data["errors"][0]["category"] == "cancelled"


def test_status_helpers_render_dynamic_text_without_markup(capsys: pytest.CaptureFixture[str]) -> None:
    info("[red]saved name[/red]")
    warn("[bold]remote output[/bold]")

    captured = capsys.readouterr().err
    assert "[red]saved name[/red]" in captured
    assert "[bold]remote output[/bold]" in captured


def _tty(input_text: str) -> MagicMock:
    """A /dev/tty stand-in that answers *input_text*."""
    tty = MagicMock()
    tty.__enter__.return_value = StringIO(input_text + "\n")
    return tty


@pytest.mark.parametrize(
    ("answer", "expected"), [("y", True), ("Y", True), ("yes", True), ("", True), ("n", False), ("N", False)]
)
def test_confirm_answers(answer: str, expected: bool) -> None:
    with patch("builtins.open", return_value=_tty(answer)):
        assert confirm("Deploy?") is expected


def test_confirm_without_a_tty_rejects() -> None:
    # CI has no /dev/tty; never assume consent.
    with patch("builtins.open", side_effect=OSError("No TTY")):
        assert confirm("Deploy?") is False


@pytest.mark.parametrize(
    ("answer", "default", "expected"),
    [("198.51.100.10", "", "198.51.100.10"), ("", "198.51.100.11", "198.51.100.11"), ("  hello  ", "", "hello")],
)
def test_prompt_answers(answer: str, default: str, expected: str) -> None:
    with patch("builtins.open", return_value=_tty(answer)):
        assert prompt("Name", default=default) == expected


@pytest.mark.parametrize("default", ["fallback", ""])
def test_prompt_without_a_tty_returns_the_default(default: str) -> None:
    with patch("builtins.open", side_effect=OSError("No TTY")):
        assert prompt("IP address", default=default) == default
