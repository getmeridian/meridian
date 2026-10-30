"""One snapshot-covered fleet language, independent of reviewed delivery settings."""

from __future__ import annotations

from meridian.control.events import EventLedger
from meridian.control.store import Store
from meridian.core.languages import DEFAULT_LANGUAGE, LanguageSettings, LanguageState


class LanguageLedger:
    def __init__(self, store: Store, events: EventLedger) -> None:
        self._store = store
        self._events = events

    def read(self) -> LanguageState:
        return LanguageState.model_validate({"language": self._store.meta("subscription_language") or DEFAULT_LANGUAGE})

    def set(self, settings: LanguageSettings) -> LanguageState:
        with self._store.transaction():
            if self.read().language == settings.language:
                return LanguageState(language=settings.language)
            self._store.set_meta("subscription_language", settings.language)
            self._events.append("language.changed", "language")
            return LanguageState(language=settings.language, changed=True)
