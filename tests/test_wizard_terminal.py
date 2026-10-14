"""Drive the wizard's terminal controls with real key sequences through prompt_toolkit's pipe input."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from prompt_toolkit.input import PipeInput, create_pipe_input
from prompt_toolkit.output import DummyOutput

from meridian.commands import wizard_terminal
from meridian.commands.wizard_prompts import Choice, Navigation, Prompter, RichWizardIO
from meridian.commands.wizard_terminal import TerminalWizardIO, wizard_io
from meridian.core.inputs import validate_hostname_value

UP, DOWN, ENTER, ESC, CTRL_C = "\x1b[A", "\x1b[B", "\r", "\x1b", "\x03"

SITES = [Choice("www.google.com", "www.google.com"), Choice("dl.google.com", "dl.google.com")]
SERVERS = [Choice("srv-a", "alpha"), Choice("srv-b", "beta"), Choice("srv-c", "gamma")]


class RecordingTerminalIO(TerminalWizardIO):
    def __init__(self, keys: PipeInput) -> None:
        super().__init__(input=keys, output=DummyOutput())
        self.lines: list[str] = []

    def write(self, message: str = "") -> None:
        self.lines.append(message)


@pytest.fixture
def keys() -> Iterator[PipeInput]:
    with create_pipe_input() as pipe:
        yield pipe


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        (ENTER, "dl.google.com"),
        (UP + ENTER, "www.google.com"),
        (DOWN + DOWN + ENTER, "dl.google.com"),
        ("1" + ENTER, "www.google.com"),
    ],
    ids=["default", "arrow-up", "wraps-around", "digit-jumps"],
)
def test_select_moves_with_arrows_and_digits(keys: PipeInput, typed: str, expected: str) -> None:
    keys.send_text(typed)
    io = RecordingTerminalIO(keys)

    assert io.select("Camouflage site", SITES, default="dl.google.com") == expected
    assert expected in io.lines[-1]


def test_select_other_row_opens_a_validated_text_field(keys: PipeInput) -> None:
    keys.send_text(DOWN + DOWN + ENTER + "not a host" + ENTER + "\x7f" * 10 + "www.example.org" + ENTER)

    answer = RecordingTerminalIO(keys).select(
        "Camouflage site", SITES, other="Type a site", validate_other=validate_hostname_value
    )

    assert answer == "www.example.org"


def test_esc_in_the_other_field_returns_to_the_list(keys: PipeInput) -> None:
    keys.send_text(DOWN + DOWN + ENTER)
    keys.send_text(ESC)
    keys.send_text(ENTER)

    assert RecordingTerminalIO(keys).select("Camouflage site", SITES, other="Type a site") == "www.google.com"


@pytest.mark.parametrize(("typed", "command"), [(ESC, "back"), (CTRL_C, "quit")], ids=["esc", "ctrl-c"])
def test_list_keys_navigate_instead_of_answering(keys: PipeInput, typed: str, command: str) -> None:
    keys.send_text(typed)

    with pytest.raises(Navigation) as raised:
        RecordingTerminalIO(keys).select("Camouflage site", SITES)

    assert raised.value.command == command


def test_multiselect_toggles_with_space_and_keeps_order(keys: PipeInput) -> None:
    keys.send_text(" " + ENTER)  # uncheck alpha

    picked = RecordingTerminalIO(keys).multiselect("Servers", SERVERS, checked=["srv-a", "srv-b", "srv-c"])

    assert picked == ["srv-b", "srv-c"]


def test_multiselect_refuses_an_empty_answer_until_something_is_checked(keys: PipeInput) -> None:
    keys.send_text("a" + ENTER + DOWN + " " + ENTER)  # 'a' clears the full set, Enter is refused, then check beta

    picked = RecordingTerminalIO(keys).multiselect("Servers", SERVERS, checked=["srv-a", "srv-b", "srv-c"])

    assert picked == ["srv-b"]


def test_multiselect_action_row_returns_the_action(keys: PipeInput) -> None:
    keys.send_text(UP + ENTER)  # wraps from the first row to the action below the list

    picked = RecordingTerminalIO(keys).multiselect(
        "Servers", SERVERS, checked=["srv-a"], actions=[Choice("add", "Add another server")]
    )

    assert picked == ["add"]


@pytest.mark.parametrize(
    ("typed", "expected"), [(ENTER, "DE"), ("NL" + ENTER, "NL")], ids=["enter-takes-default", "typed-wins"]
)
def test_text_shows_the_default_and_enter_takes_it(keys: PipeInput, typed: str, expected: str) -> None:
    keys.send_text(typed)

    assert RecordingTerminalIO(keys).text("Country", default="DE") == expected


def test_text_keeps_asking_until_the_validator_accepts(keys: PipeInput) -> None:
    keys.send_text("bad" + ENTER + "\x7f\x7f\x7f" + "vpn.example.com" + ENTER)

    assert RecordingTerminalIO(keys).text("Domain", validate=validate_hostname_value) == "vpn.example.com"


def test_navigation_words_pass_validation_and_still_navigate(keys: PipeInput) -> None:
    keys.send_text("back" + ENTER)
    prompter = Prompter(RecordingTerminalIO(keys))

    with pytest.raises(Navigation) as raised:
        prompter.ask("Domain", validate=validate_hostname_value)

    assert raised.value.command == "back"


class PlainIO:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.lines: list[str] = []

    def write(self, message: str = "") -> None:
        self.lines.append(message)

    def ask(self, label: str, *, default: str = "") -> str:
        return self.answers.pop(0) or default


def test_plain_prompts_reask_after_a_validation_error() -> None:
    io = PlainIO("bad", "vpn.example.com")

    assert Prompter(io).ask("Domain", validate=validate_hostname_value) == "vpn.example.com"
    assert any("domain" in line.lower() for line in io.lines)


@pytest.mark.parametrize(
    ("answers", "expected"),
    [(("2",), "dl.google.com"), (("",), "www.google.com"), (("no dots", "www.example.org"), "www.example.org")],
    ids=["number", "default", "typed-site-after-a-bad-one"],
)
def test_plain_choose_accepts_a_typed_value_when_other_is_allowed(answers: tuple[str, ...], expected: str) -> None:
    prompter = Prompter(PlainIO(*answers))

    assert prompter.choose("Site", SITES, other="Type a site", validate_other=validate_hostname_value) == expected


def test_pickers_are_used_only_by_terminal_io(keys: PipeInput) -> None:
    assert Prompter(RichWizardIO()).pickers is None
    assert Prompter(RecordingTerminalIO(keys)).pickers is not None


@pytest.mark.parametrize(
    ("env", "tty"),
    [({"MERIDIAN_PLAIN_PROMPTS": "1"}, True), ({"TERM": "dumb"}, True), ({}, False)],
    ids=["plain-env", "dumb-terminal", "no-terminal"],
)
def test_wizard_io_falls_back_to_numbered_prompts(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], tty: bool
) -> None:
    monkeypatch.delenv("MERIDIAN_PLAIN_PROMPTS", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(wizard_terminal.sys.stderr, "isatty", lambda: tty, raising=False)

    assert type(wizard_io()) is RichWizardIO
