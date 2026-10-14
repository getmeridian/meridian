"""Interactive wizard controls: arrow-key lists, checklists, and validated text fields.

Built on prompt_toolkit, reading keys from /dev/tty and drawing on stderr like the
plain prompts, so `setup` still works with stdin piped. `wizard_io()` picks these
controls only for a capable terminal; everything else gets the numbered prompts.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.input import Input, create_input
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import Output, create_output
from prompt_toolkit.styles import Style
from prompt_toolkit.validation import ValidationError, Validator
from rich.markup import escape

from meridian.i18n import t

from .wizard_prompts import Choice, Navigation, RichWizardIO, Validate, WizardIO

if TYPE_CHECKING:
    from collections.abc import Iterator

PLAIN_ENV = "MERIDIAN_PLAIN_PROMPTS"

# Close to the Rich theme the rest of the CLI uses: cyan for focus, dim for hints.
STYLE = Style.from_dict(
    {
        "label": "bold",
        "pointer": "ansicyan bold",
        "focused": "ansicyan",
        "checked": "ansigreen",
        "hint": "ansibrightblack",
        "help": "ansibrightblack italic",
        "details": "ansibrightblack",
        "error": "ansired",
    }
)


def wizard_io() -> WizardIO:
    """Interactive controls in a real terminal; numbered prompts otherwise."""
    if os.environ.get(PLAIN_ENV, "").strip() not in {"", "0"} or os.environ.get("TERM") == "dumb":
        return RichWizardIO()
    if not sys.stderr.isatty():
        return RichWizardIO()
    try:
        with open("/dev/tty"):
            pass
    except OSError:
        return RichWizardIO()
    return TerminalWizardIO()


def _terminal_device() -> str:
    """The terminal's real device path: macOS's kqueue refuses the /dev/tty alias (EOFError)."""
    for stream in (sys.stdin, sys.stderr, sys.stdout):
        try:
            if stream.isatty():
                return os.ttyname(stream.fileno())
        except (AttributeError, OSError, ValueError):
            continue
    return "/dev/tty"


@dataclass
class _ListState:
    """Cursor and selection for a list; actions sit below the options."""

    choices: Sequence[Choice]
    actions: Sequence[Choice] = ()
    multi: bool = False
    cursor: int = 0
    checked: set[int] = field(default_factory=set)
    show_details: bool = False
    error: str = ""

    @property
    def rows(self) -> int:
        return len(self.choices) + len(self.actions)

    def move(self, step: int) -> None:
        self.cursor = (self.cursor + step) % self.rows
        self.error = ""

    def on_action(self) -> bool:
        return self.cursor >= len(self.choices)


class TerminalWizardIO(RichWizardIO):
    """`WizardIO` with pickers. `input`/`output` are injectable so tests can type keys."""

    def __init__(self, *, input: Input | None = None, output: Output | None = None) -> None:
        self._input = input
        self._output = output

    @contextmanager
    def _terminal(self) -> Iterator[tuple[Input, Output]]:
        if self._input is not None and self._output is not None:
            yield self._input, self._output
            return
        with open(_terminal_device()) as tty:
            yield create_input(stdin=tty), create_output(stdout=sys.stderr)

    def ask(self, label: str, *, default: str = "") -> str:
        return self.text(label, default=default)

    def text(self, label: str, *, default: str = "", validate: Validate | None = None) -> str:
        """One line of input. The default shows greyed and Enter takes it; errors appear below."""
        bindings = KeyBindings()

        @bindings.add("escape", eager=True)
        def _back(event: KeyPressEvent) -> None:
            event.app.exit(exception=Navigation("back"))

        @bindings.add("c-c")
        @bindings.add("c-d")
        def _quit(event: KeyPressEvent) -> None:
            event.app.exit(exception=Navigation("quit"))

        with self._terminal() as (terminal_input, terminal_output):
            session: PromptSession[str] = PromptSession(
                input=terminal_input,
                output=terminal_output,
                key_bindings=bindings,
                style=STYLE,
                validator=_validator(validate, default) if validate else None,
                validate_while_typing=False,
            )
            session.app.ttimeoutlen = 0.05  # Esc answers at once instead of waiting for a key sequence
            answer = session.prompt(
                [("class:pointer", "  → "), ("class:label", f"{label}: ")],
                placeholder=[("class:hint", default)] if default else None,
            )
        return answer or default

    def select(
        self,
        label: str,
        choices: Sequence[Choice],
        *,
        default: str = "",
        other: str = "",
        validate_other: Validate | None = None,
        details: str = "",
    ) -> str:
        """Arrow-key list. With `other`, a last row lets the reader type their own value."""
        actions = [Choice("", t("picker.other"))] if other else []
        values = [choice.value for choice in choices]
        while True:
            state = _ListState(choices, actions, cursor=values.index(default) if default in values else 0)
            picked = self._run_list(label, state, details=details, help_key="picker.help_select")
            if not state.on_action():
                self.write(_summary(label, choices[picked[0]].label))
                return choices[picked[0]].value
            try:
                return self.text(other, validate=validate_other)
            except Navigation as navigation:
                if navigation.command != "back":
                    raise
                # Esc in the text field returns to the list.

    def multiselect(
        self,
        label: str,
        choices: Sequence[Choice],
        *,
        checked: Sequence[str] = (),
        actions: Sequence[Choice] = (),
        details: str = "",
    ) -> list[str]:
        """Checklist; returns checked values, or `[action.value]` when an action row is chosen."""
        state = _ListState(
            choices,
            actions,
            multi=True,
            checked={index for index, choice in enumerate(choices) if choice.value in checked},
        )
        picked = self._run_list(label, state, details=details, help_key="picker.help_multi")
        if state.on_action():
            action = actions[state.cursor - len(choices)]
            self.write(_summary(label, action.label))
            return [action.value]
        self.write(_summary(label, ", ".join(choices[index].label for index in picked)))
        return [choices[index].value for index in picked]

    def _run_list(self, label: str, state: _ListState, *, details: str, help_key: str) -> list[int]:
        bindings = KeyBindings()

        @bindings.add("up")
        @bindings.add("k")
        @bindings.add("s-tab")
        def _up(event: KeyPressEvent) -> None:
            state.move(-1)

        @bindings.add("down")
        @bindings.add("j")
        @bindings.add("tab")
        def _down(event: KeyPressEvent) -> None:
            state.move(1)

        for digit in "123456789":

            @bindings.add(digit)
            def _jump(event: KeyPressEvent) -> None:
                index = int(event.data) - 1
                if index < len(state.choices):
                    state.cursor, state.error = index, ""

        @bindings.add("?")
        def _details(event: KeyPressEvent) -> None:
            state.show_details = not state.show_details

        if state.multi:

            @bindings.add("space")
            def _toggle(event: KeyPressEvent) -> None:
                if not state.on_action():
                    state.checked ^= {state.cursor}
                    state.error = ""

            @bindings.add("a")
            def _all(event: KeyPressEvent) -> None:
                every = set(range(len(state.choices)))
                state.checked = set() if state.checked == every else every
                state.error = ""

        @bindings.add("enter")
        @bindings.add("c-j")  # a bare line feed, as some terminals and pexpect's sendline send
        def _enter(event: KeyPressEvent) -> None:
            if state.on_action() or not state.multi:
                event.app.exit(result=[state.cursor])
            elif state.checked:
                event.app.exit(result=sorted(state.checked))
            else:
                state.error = t("picker.none_checked")

        @bindings.add("escape", eager=True)
        def _back(event: KeyPressEvent) -> None:
            event.app.exit(exception=Navigation("back"))

        @bindings.add("c-c")
        @bindings.add("c-d")
        def _quit(event: KeyPressEvent) -> None:
            event.app.exit(exception=Navigation("quit"))

        control = FormattedTextControl(lambda: _render(label, state, details, help_key), focusable=True)
        with self._terminal() as (terminal_input, terminal_output):
            app: Application[list[int]] = Application(
                layout=Layout(HSplit([Window(control, wrap_lines=True, dont_extend_height=True)])),
                key_bindings=bindings,
                style=STYLE,
                erase_when_done=True,
                input=terminal_input,
                output=terminal_output,
            )
            app.ttimeoutlen = 0.05
            return app.run()


def _render(label: str, state: _ListState, details: str, help_key: str) -> StyleAndTextTuples:
    lines: StyleAndTextTuples = [("class:pointer", "  ? "), ("class:label", label), ("", "\n")]
    rows = [*state.choices, *state.actions]
    for index, choice in enumerate(rows):
        focused = index == state.cursor
        if index == len(state.choices) and state.choices:
            lines.append(("class:hint", "    ─────\n"))
        lines.append(("class:pointer", "  › " if focused else "    "))
        if state.multi and index < len(state.choices):
            mark = "[x] " if index in state.checked else "[ ] "
            lines.append(("class:checked" if index in state.checked else "", mark))
        lines.append(("class:focused" if focused else "", choice.label))
        if choice.hint:
            lines.append(("class:hint", f"  {choice.hint}"))
        lines.append(("", "\n"))
    if state.error:
        lines.append(("class:error", f"  {state.error}\n"))
    help_text = t(help_key) + (f" · {t('picker.help_details')}" if details else "")
    lines.append(("class:help", f"  {help_text}"))
    if state.show_details and details:
        lines.append(("", "\n"))
        lines.extend(("class:details", f"  {line}\n") for line in details.splitlines())
    return lines


def _summary(label: str, answer: str) -> str:
    return f"  [info]→[/info] {escape(label)}: [bold]{escape(answer)}[/bold]"


def _validator(validate: Validate, default: str) -> Validator:
    class _Check(Validator):
        def validate(self, document: Any) -> None:
            try:
                validate(document.text or default)
            except ValueError as exc:
                raise ValidationError(message=str(exc), cursor_position=len(document.text)) from exc

    return _Check()


__all__ = ["PLAIN_ENV", "TerminalWizardIO", "wizard_io"]
