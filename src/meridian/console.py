"""Rich-based terminal output helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, NoReturn, cast

import typer
from rich.console import Console
from rich.markup import escape
from rich.theme import Theme

_theme = Theme(
    {
        "info": "cyan",
        "ok": "green",
        "warn": "yellow",
        "error": "red bold",
        "dim": "dim",
    }
)


class ConsoleState:
    """Encapsulates mutable console globals for testability."""

    __slots__ = ("console", "err_console", "json_mode", "quiet_mode")

    def __init__(self) -> None:
        self.console = Console(theme=_theme, highlight=False)
        self.err_console = Console(theme=_theme, stderr=True, highlight=False)
        self.json_mode: bool = False
        self.quiet_mode: bool = False


_state = ConsoleState()

# Module-level aliases for backward compatibility.
console = _state.console
err_console = _state.err_console

_error_command: ContextVar[str] = ContextVar("meridian_error_command", default="cli.error")
_error_timer: ContextVar[Any | None] = ContextVar("meridian_error_timer", default=None)


def set_json_mode(enabled: bool) -> None:
    """Set JSON output mode."""
    _state.json_mode = enabled


def is_json_mode() -> bool:
    """Check if JSON output mode is active."""
    return _state.json_mode


def set_quiet_mode(enabled: bool) -> None:
    """Set quiet mode — suppresses info/ok/warn/banner output."""
    _state.quiet_mode = enabled


def is_quiet_mode() -> bool:
    """Check if quiet mode is active."""
    return _state.quiet_mode


@contextmanager
def error_context(command: str, *, timer: Any | None = None) -> Iterator[None]:
    """Attach command metadata to JSON errors emitted by fail()."""
    command_token = _error_command.set(command)
    timer_token = _error_timer.set(timer)
    try:
        yield
    finally:
        _error_command.reset(command_token)
        _error_timer.reset(timer_token)


def json_output(data: Any) -> None:
    """Write JSON to stdout (for --json mode). Separate from Rich stderr output."""
    from meridian.renderers import json_output as render_json

    render_json(data)


def info(msg: str) -> None:
    if not _state.quiet_mode:
        err_console.print(f"  [info]\u2192[/info] {msg}")


def ok(msg: str) -> None:
    if not _state.quiet_mode:
        err_console.print(f"  [ok]\u2713[/ok] {msg}")


def warn(msg: str) -> None:
    if not _state.quiet_mode:
        err_console.print(f"  [warn]![/warn] {msg}")


_EXIT_CODES = {"user": 2, "system": 3, "bug": 1, "cancelled": 130}


def fail(
    msg: str | Exception,
    *,
    hint: str = "",
    hint_type: str = "bug",
    exit_code: int | None = None,
) -> NoReturn:
    """Print an error message and exit.

    Args:
        msg: The error message to display, or a ``MeridianError`` instance
            whose fields are used directly (hint/hint_type/exit_code kwargs
            are ignored when a ``MeridianError`` is passed).
        hint: Optional hint shown below the error.
        hint_type: Controls the footer line shown:
            "user"   -- input validation errors; no GitHub link shown.
            "system" -- infrastructure errors; suggests 'meridian doctor'.
            "bug"    -- unexpected errors (default); shows GitHub issues link.
        exit_code: Explicit exit code. If None, derived from hint_type
            (user=2, system=3, bug=1).
    """
    from meridian.core.errors import MeridianError as _MeridianError

    if isinstance(msg, _MeridianError):
        hint = msg.hint
        hint_type = msg.category
        exit_code = msg.exit_code
        msg = str(msg)
    code = exit_code if exit_code is not None else _EXIT_CODES.get(hint_type, 1)
    if _state.json_mode:
        from meridian.core.models import ErrorCategory, MeridianError
        from meridian.core.output import command_envelope
        from meridian.renderers import emit_json

        category = cast(ErrorCategory, hint_type if hint_type in ("user", "system", "bug", "cancelled") else "bug")
        emit_json(
            command_envelope(
                command=_error_command.get(),
                summary=msg,
                status="cancelled" if category == "cancelled" else "failed",
                exit_code=code,
                errors=[
                    MeridianError(
                        code=f"MERIDIAN_{category.upper()}_ERROR",
                        category=category,
                        message=msg,
                        hint=hint,
                        retryable=category == "system",
                        exit_code=code,
                    )
                ],
                timer=_error_timer.get(),
            )
        )
        raise typer.Exit(code=code)

    err_console.print(f"\n  [error]\u2717 {msg}[/error]")
    if hint:
        err_console.print(f"  [dim]{hint}[/dim]")
    if hint_type in ("user", "cancelled"):
        err_console.print()
    elif hint_type == "system":
        err_console.print("  [dim]Run: meridian doctor  (to collect server info)[/dim]\n")
    else:  # "bug"
        err_console.print("  [dim]Report: https://github.com/uburuntu/meridian/issues[/dim]\n")
    raise typer.Exit(code=code)


def line() -> None:
    dash = "\u2500"
    err_console.print(f"  [dim]{dash * 41}[/dim]")


def banner(version: str) -> None:
    if not _state.quiet_mode:
        err_console.print(f"\n  [bold]Meridian[/bold] [dim]v{version}[/dim]\n")


def prompt(message: str, default: str = "") -> str:
    """Interactive prompt that reads from /dev/tty for pipe safety."""
    suffix = f" [{default}]" if default else ""
    # Escape message and suffix to prevent Rich from parsing [y/N] etc. as markup
    escaped = escape(f"{message}{suffix}")
    try:
        with open("/dev/tty") as tty:
            err_console.print(f"  [info]\u2192[/info] {escaped}: ", end="")
            value = tty.readline().strip()
    except OSError:
        # No TTY available (CI, non-interactive)
        value = ""
    return value or default


def confirm(message: str = "Continue?") -> bool:
    """Y/n confirmation prompt. Returns True on accept, False on reject."""
    try:
        with open("/dev/tty") as tty:
            err_console.print(f"\n  [info]\u2192[/info] {message} [dim][Y/n][/dim] ", end="")
            answer = tty.readline().strip().lower()
    except OSError:
        return False
    return answer in ("", "y", "yes")


def choose(message: str, options: list[str], *, default: int = 1) -> int:
    """Numbered choice prompt. Returns 1-based index of selected option.

    Displays numbered options, then prompts for selection.
    Falls back to default on empty input or invalid choice.
    """
    for i, opt in enumerate(options, 1):
        err_console.print(f"    {i}. {opt}")
    err_console.print()
    answer = prompt(f"{message}, or press Enter for ({default})")
    if not answer:
        return default
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return int(answer)
    return default
