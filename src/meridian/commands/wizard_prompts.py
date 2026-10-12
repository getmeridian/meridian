"""Prompt primitives for the setup wizard: navigation words, yes/no, numbers, and choices.

Every word the user can type (yes, back, all, ...) is accepted in English and in the
chosen language, so a mistyped keyboard layout never strands anyone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from rich.markup import escape

from meridian.console import confirm, err_console, prompt
from meridian.i18n import LOCALE_NAMES, LOCALES, Locale, system_locale, t, words


class WizardIO(Protocol):
    def write(self, message: str = "") -> None: ...

    def ask(self, label: str, *, default: str = "") -> str: ...

    def approve(self, label: str) -> bool: ...


class RichWizardIO:
    """Typer/Rich-backed input adapter."""

    def write(self, message: str = "") -> None:
        err_console.print(message)

    def ask(self, label: str, *, default: str = "") -> str:
        return prompt(label, default=default)

    def approve(self, label: str) -> bool:
        return confirm(label)


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
        self.details = ""

    def say(self, text: str, *, style: str = "") -> None:
        body = escape(text)
        self.io.write(f"[{style}]{body}[/{style}]" if style else body)

    def explain(self, text: str) -> None:
        for line in text.splitlines():
            self.say(f"  {line}" if line else "", style="dim")

    def ask(self, label: str, *, default: str = "") -> str:
        while True:
            value = self.io.ask(label, default=default).strip()
            command = value.lower()
            if command in words("word.details"):
                self.explain(self.details)
                continue
            for name in _NAVIGATION:
                if command in words(f"word.{name}"):
                    raise Navigation(name)
            return value

    def yes_no(self, label: str, *, default: bool, hint: str = "") -> bool:
        if hint:
            self.explain(hint)
        yes, no = primary_word("word.yes"), primary_word("word.no")
        answer = self.ask(f"{label} [{yes}/{no}]", default=yes if default else no).lower()
        if answer in words("word.yes"):
            return True
        if answer in words("word.no"):
            return False
        raise ValueError(t("error.yes_no", yes=yes, no=no))

    def numbers(self, label: str, count: int, *, default: str = "", allow_all: bool = False) -> list[int]:
        answer = self.ask(label, default=default)
        if allow_all and answer.lower() in words("word.all"):
            return list(range(1, count + 1))
        return parse_numbers(answer, count)

    def choose(self, label: str, choices: Sequence[Choice], *, default: str = "") -> str:
        """Show a numbered list and return the chosen value (by number or by value)."""
        for index, choice in enumerate(choices, 1):
            hint = f" — {choice.hint}" if choice.hint else ""
            self.io.write(f"  [bold]{index}[/bold]) {escape(choice.label)}[dim]{escape(hint)}[/dim]")
        values = [choice.value for choice in choices]
        default_number = str(values.index(default) + 1) if default in values else "1"
        answer = self.ask(label, default=default_number)
        if answer in values:
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
