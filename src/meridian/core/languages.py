"""The four supported languages, shared by presentation and service preferences."""

from __future__ import annotations

from typing import Literal, get_args

from meridian.core.errors import MeridianError
from meridian.core.models import CoreModel

Language = Literal["en", "ru", "fa", "zh"]
LANGUAGES: tuple[Language, ...] = get_args(Language)
LANGUAGE_NAMES: dict[Language, str] = {"en": "English", "ru": "Русский", "fa": "فارسی", "zh": "中文"}
DEFAULT_LANGUAGE: Language = "en"
FleetLanguage = Literal["auto", Language]
FLEET_LANGUAGES: tuple[FleetLanguage, ...] = get_args(FleetLanguage)
DEFAULT_FLEET_LANGUAGE: FleetLanguage = "auto"


class LanguageSettings(CoreModel):
    """Starting page and app status language; auto follows browsers and keeps app messages English."""

    language: FleetLanguage = DEFAULT_FLEET_LANGUAGE


class LanguageState(LanguageSettings):
    changed: bool = False


class LanguageError(MeridianError):
    def __init__(self, *, fleet: bool = False) -> None:
        choices = "auto, en, ru, fa, or zh" if fleet else "en, ru, fa, or zh"
        super().__init__(f"Choose {choices}.", category="user")


def parse_language(text: str) -> Language:
    for language in LANGUAGES:
        if text == language:
            return language
    raise LanguageError()


def parse_person_language(text: str) -> Language | None:
    return None if text == "default" else parse_language(text)


def parse_fleet_language(text: str) -> FleetLanguage:
    for language in FLEET_LANGUAGES:
        if text == language:
            return language
    raise LanguageError(fleet=True)


def resolve_language(fleet: FleetLanguage, person: Language | None = None) -> FleetLanguage:
    """A person override wins; browser detection belongs only to the page."""
    return person if person is not None else fleet


def language_or_english(language: FleetLanguage) -> Language:
    """Native status remarks and initial auto HTML use English without inspecting requests."""
    return DEFAULT_LANGUAGE if language == "auto" else language
