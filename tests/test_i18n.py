"""Message catalog parity, language detection, and localized answers."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from meridian import i18n
from meridian.commands.wizard_prompts import Navigation, Prompter

SRC = Path(__file__).resolve().parents[1] / "src" / "meridian"
_PLACEHOLDER = re.compile(r"{(\w+)}")
# Keys built at runtime: stage titles/details, routing match labels, reply words.
_DYNAMIC = re.compile(r'\bt\(f"(stage|details|routing\.match|word)[._]\{')


@pytest.fixture(autouse=True)
def _english(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(i18n, "_requested", None)
    monkeypatch.delenv("MERIDIAN_LANG", raising=False)
    i18n.set_locale("en")
    yield
    i18n.set_locale("en")


@pytest.mark.parametrize("locale", [code for code in i18n.LOCALES if code != "en"])
def test_every_locale_has_every_key_with_the_same_placeholders(locale: i18n.Locale) -> None:
    english = i18n.catalog("en")
    translated = i18n.catalog(locale)
    assert list(translated) == list(english)
    for key, text in english.items():
        assert sorted(_PLACEHOLDER.findall(translated[key])) == sorted(_PLACEHOLDER.findall(text)), key
        assert translated[key].strip(), key


@pytest.mark.parametrize("locale", i18n.LOCALES)
def test_reply_words_never_collide_or_shadow_role_names(locale: i18n.Locale) -> None:
    owners: dict[str, str] = {}
    messages = i18n.catalog(locale)
    roles = {"exit", "relay", "gateway", "control"} | {
        messages[f"role.{name}"].lower() for name in ("exit", "relay", "gateway", "control")
    }
    for key, text in messages.items():
        if not key.startswith("word."):
            continue
        for form in text.split("|"):
            word = form.strip().lower()
            assert word not in roles, (key, word)
            assert owners.setdefault(word, key) == key, f"{word!r} used by {owners[word]} and {key}"


def test_every_literal_key_used_in_code_exists_in_english() -> None:
    english = i18n.catalog("en")
    used: set[str] = set()
    for path in SRC.rglob("*.py"):
        used |= set(re.findall(r'\b(?:t|words|primary_word)\("([a-z_]+\.[a-z_.]+)"', path.read_text(encoding="utf-8")))
    assert used, "no catalog keys found"
    assert sorted(used - set(english)) == []


def test_dynamic_key_families_are_complete() -> None:
    from meridian.core.setup import SETUP_STAGE_ORDER

    english = i18n.catalog("en")
    for stage in SETUP_STAGE_ORDER:
        assert f"stage.{stage}" in english and f"details.{stage}" in english
    for kind in ("country", "domain", "ip", "network"):
        assert f"routing.match_{kind}" in english
    for name in ("back", "save", "quit", "details", "yes", "no", "all", "add", "none"):
        assert f"word.{name}" in english
    wizard = (SRC / "commands" / "setup_wizard.py").read_text(encoding="utf-8")
    assert _DYNAMIC.search(wizard)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ru", "ru"),
        ("ru_RU.UTF-8", "ru"),
        ("zh-Hans", "zh"),
        ("fa_IR", "fa"),
        ("EN", "en"),
        ("de_DE", None),
        ("", None),
    ],
)
def test_normalize_locale_accepts_posix_and_bcp47_forms(value: str, expected: str | None) -> None:
    assert i18n.normalize_locale(value) == expected


def test_lang_flag_beats_environment_which_beats_saved_choice(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert i18n.explicit_locale() is None
    i18n.save_locale("zh")
    assert i18n.explicit_locale() == "zh"
    monkeypatch.setenv("MERIDIAN_LANG", "fa_IR.UTF-8")
    assert i18n.explicit_locale() == "fa"
    i18n.request_locale("ru")
    assert i18n.explicit_locale() == "ru"
    assert (tmp_home / "preferences.json").read_text(encoding="utf-8").count("zh") == 1


def test_system_locale_reads_lc_all_before_lang(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANG", "ru_RU.UTF-8")
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LC_ALL", "zh_CN.UTF-8")
    assert i18n.system_locale() == "zh"
    monkeypatch.setenv("LC_ALL", "C")
    assert i18n.system_locale() == "en"


def test_corrupt_preferences_file_is_ignored(tmp_home: Path) -> None:
    (tmp_home / "preferences.json").write_text("[not json", encoding="utf-8")
    assert i18n.explicit_locale() is None
    i18n.save_locale("ru")
    assert i18n.saved_locale() == "ru"


class _IO:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.output: list[str] = []
        self.labels: list[str] = []

    def write(self, message: str = "") -> None:
        self.output.append(message)

    def ask(self, label: str, *, default: str = "") -> str:
        self.labels.append(label)
        return self.answers.pop(0) or default


def test_russian_prompts_accept_russian_and_english_answers() -> None:
    i18n.set_locale("ru")
    io = _IO("да", "no", "назад")
    prompt = Prompter(io)

    assert prompt.yes_no("?", default=False) is True
    assert prompt.yes_no("?", default=True) is False
    with pytest.raises(Navigation) as navigation:
        prompt.ask("?")
    assert navigation.value.command == "back"
    assert "[да/нет]" in io.labels[0]


def test_persian_digits_select_numbered_choices() -> None:
    from meridian.commands.wizard_prompts import Choice

    io = _IO("۲")
    assert Prompter(io).choose("?", [Choice("a", "A"), Choice("b", "B")]) == "b"


def test_language_step_lists_every_language_and_defaults_to_the_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LC_ALL", "fa_IR.UTF-8")
    io = _IO("")
    assert Prompter(io).language() == "fa"
    assert all(name in "\n".join(io.output) for name in i18n.LOCALE_NAMES.values())
