"""The four supported languages, shared by presentation and service preferences."""

from __future__ import annotations

from typing import Literal, get_args

from meridian.core.errors import MeridianError
from meridian.core.models import CoreModel

Language = Literal["en", "ru", "fa", "zh"]
LANGUAGES: tuple[Language, ...] = get_args(Language)
LANGUAGE_NAMES: dict[Language, str] = {"en": "English", "ru": "Русский", "fa": "فارسی", "zh": "中文"}
DEFAULT_LANGUAGE: Language = "en"


class LanguageSettings(CoreModel):
    """Operator-owned default for native subscription presentation; never guessed from requests."""

    language: Language = DEFAULT_LANGUAGE


class LanguageState(LanguageSettings):
    changed: bool = False


class LanguageError(MeridianError):
    def __init__(self) -> None:
        super().__init__("Choose en, ru, fa, or zh.", category="user")


def parse_language(text: str) -> Language:
    for language in LANGUAGES:
        if text == language:
            return language
    raise LanguageError()


def parse_person_language(text: str) -> Language | None:
    return None if text == "default" else parse_language(text)
