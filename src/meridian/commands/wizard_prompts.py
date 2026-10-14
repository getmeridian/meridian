"""Prompt primitives for the setup wizard: navigation words, yes/no, numbers, and choices.

Every word the user can type (yes, back, all, ...) is accepted in English and in the
chosen language, so a mistyped keyboard layout never strands anyone.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from rich.markup import escape

from meridian.console import err_console, prompt
from meridian.i18n import LOCALE_NAMES, LOCALES, Locale, system_locale, t, words


class WizardIO(Protocol):
    def write(self, message: str = "") -> None: ...

    def ask(self, label: str, *, default: str = "") -> str: ...


Validate = Callable[[str], object]
"""Raises ValueError with a readable message when the answer is unusable."""


@runtime_checkable
class Pickers(Protocol):
    """Optional `WizardIO` controls for a capable terminal (`wizard_terminal.py`)."""

    def text(self, label: str, *, default: str = "", validate: Validate | None = None) -> str: ...

    def select(
        self,
        label: str,
        choices: Sequence[Choice],
        *,
        default: str = "",
        other: str = "",
        validate_other: Validate | None = None,
        details: str = "",
    ) -> str: ...

    def multiselect(
        self,
        label: str,
        choices: Sequence[Choice],
        *,
        checked: Sequence[str] = (),
        actions: Sequence[Choice] = (),
        details: str = "",
    ) -> list[str]: ...


class RichWizardIO:
    """Typer/Rich-backed input adapter."""

    def write(self, message: str = "") -> None:
        err_console.print(message)

    def ask(self, label: str, *, default: str = "") -> str:
        return prompt(label, default=default)


class Navigation(Exception):
    """The user typed back, save, or quit."""

    def __init__(self, command: str) -> None:
        self.command = command
        super().__init__(command)


@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    hint: str = ""


_NAVIGATION = ("back", "save", "quit")


def primary_word(key: str) -> str:
    """The form shown in prompts, e.g. ``да`` for ``word.yes`` in Russian."""
    return t(key).split("|")[0]


class Prompter:
    """Asks one question at a time and turns navigation words into ``Navigation``."""

    def __init__(self, io: WizardIO) -> None:
        self.io = io
        self.pickers = io if isinstance(io, Pickers) else None
        self.details = ""

    def say(self, text: str, *, style: str = "") -> None:
        body = escape(text)
        self.io.write(f"[{style}]{body}[/{style}]" if style else body)

    def explain(self, text: str) -> None:
        for line in text.splitlines():
            self.say(f"  {line}" if line else "", style="dim")

    def ask(self, label: str, *, default: str = "", validate: Validate | None = None) -> str:
        """Read one answer; navigation words raise ``Navigation`` and ``validate`` re-asks."""
        while True:
            if self.pickers is not None:
                value = self.pickers.text(label, default=default, validate=_unless_word(validate))
            else:
                value = self.io.ask(label, default=default)
            value = value.strip()
            command = value.lower()
            if command in words("word.details"):
                self.explain(self.details)
                continue
            for name in _NAVIGATION:
                if command in words(f"word.{name}"):
                    raise Navigation(name)
            if validate is not None and self.pickers is None:
                try:
                    validate(value)
                except ValueError as exc:
                    self.say(str(exc), style="error")
                    continue
            return value

    def yes_no(self, label: str, *, default: bool, hint: str = "") -> bool:
        if hint:
            self.explain(hint)
        yes, no = primary_word("word.yes"), primary_word("word.no")
        while True:
            answer = self.ask(f"{label} [{yes}/{no}]", default=yes if default else no).lower()
            if answer in words("word.yes"):
                return True
            if answer in words("word.no"):
                return False
            self.say(t("error.yes_no", yes=yes, no=no), style="error")

    def numbers(self, label: str, count: int, *, default: str = "", allow_all: bool = False) -> list[int]:
        answer = self.ask(label, default=default)
        if allow_all and answer.lower() in words("word.all"):
            return list(range(1, count + 1))
        return parse_numbers(answer, count)

    def choose(
        self,
        label: str,
        choices: Sequence[Choice],
        *,
        default: str = "",
        other: str = "",
        validate_other: Validate | None = None,
    ) -> str:
        """Pick one value; with ``other`` the reader may type their own, checked by ``validate_other``."""
        if self.pickers is not None:
            return self.pickers.select(
                label, choices, default=default, other=other, validate_other=validate_other, details=self.details
            )
        for index, choice in enumerate(choices, 1):
            hint = f" — {choice.hint}" if choice.hint else ""
            self.io.write(f"  [bold]{index}[/bold]) {escape(choice.label)}[dim]{escape(hint)}[/dim]")
        values = [choice.value for choice in choices]
        default_number = str(values.index(default) + 1) if default in values else "1"
        while True:
            answer = self.ask(label, default=default_number)
            if answer in values:
                return answer
            if other and not answer.isdigit():
                try:
                    if validate_other is not None:
                        validate_other(answer)
                except ValueError as exc:
                    self.say(str(exc), style="error")
                    continue
                return answer
            numbers = parse_numbers(answer, len(choices))
            if len(numbers) != 1:
                raise ValueError(t("error.one_number"))
            return values[numbers[0] - 1]

    def language(self) -> Locale:
        """Step zero: pick the wizard language (shown in every language at once)."""
        choices = [Choice(code, LOCALE_NAMES[code]) for code in LOCALES]
        while True:
            try:
                return choose_locale(self.choose("Language / Язык / زبان / 语言", choices, default=system_locale()))
            except ValueError:
                continue


def _unless_word(validate: Validate | None) -> Validate | None:
    """Let navigation and details words through the picker's validation; ``ask`` acts on them."""
    if validate is None:
        return None
    reserved = {word for key in (*_NAVIGATION, "details") for word in words(f"word.{key}")}

    def check(value: str) -> object:
        return None if value.strip().lower() in reserved else validate(value)

    return check


def choose_locale(value: str) -> Locale:
    for code in LOCALES:
        if code == value:
            return code
    raise ValueError(value)


def parse_numbers(value: str, count: int) -> list[int]:
    """Parse ``1, 3`` into distinct 1-based indices within ``count``."""
    try:
        values = [int(part.strip()) for part in value.replace("،", ",").split(",") if part.strip()]
    except ValueError as exc:
        raise ValueError(t("error.numbers")) from exc
    if not values or any(index < 1 or index > count for index in values):
        raise ValueError(t("error.number_range", count=count))
    if len(values) != len(set(values)):
        raise ValueError(t("error.number_twice"))
    return values
