"""Message catalog for user-facing text: the setup wizard first, then hints and errors.

Catalogs are flat JSON files (``locales/<code>.json``) keyed by dotted message IDs
with ``{name}`` placeholders. English is the source; a missing key in another
locale falls back to English, and ``tests/test_i18n.py`` keeps the files in parity.
"""

from __future__ import annotations

import json
import os
import re
from functools import cache
from pathlib import Path
from typing import cast

from meridian.core.languages import DEFAULT_LANGUAGE as DEFAULT_LOCALE
from meridian.core.languages import LANGUAGE_NAMES
from meridian.core.languages import LANGUAGES as LOCALES
from meridian.core.languages import Language as Locale

LOCALE_NAMES = LANGUAGE_NAMES

_CATALOG_DIR = Path(__file__).parent / "locales"
_current: Locale = DEFAULT_LOCALE
_requested: Locale | None = None


@cache
def catalog(locale: Locale) -> dict[str, str]:
    """Return one locale's messages."""
    data = json.loads((_CATALOG_DIR / f"{locale}.json").read_text(encoding="utf-8"))
    return cast(dict[str, str], data)


def t(key: str, **values: object) -> str:
    """Return the current locale's text for ``key`` with placeholders filled."""
    text = catalog(_current).get(key) or catalog(DEFAULT_LOCALE)[key]
    return text.format(**values) if values else text


def isolate(text: str) -> str:
    """Keep a left-to-right name intact inside right-to-left text; other locales print it bare."""
    return f"\u2068{text}\u2069" if _current == "fa" else text


def step_label(name: str) -> str:
    """A provisioning step's display name: ``step.<slug>`` when cataloged, else its English name.

    Events keep the English name (it is part of the machine contract); only renderers translate.
    """
    key = "step." + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return catalog(_current).get(key) or catalog(DEFAULT_LOCALE).get(key) or name


def words(key: str) -> set[str]:
    """Accepted answers for a reply word: English always, plus the current locale's forms."""
    forms = {catalog(DEFAULT_LOCALE)[key], catalog(_current).get(key, "")}
    return {form.strip().lower() for value in forms for form in value.split("|") if form.strip()}


def set_locale(locale: Locale) -> None:
    global _current
    _current = locale


def normalize_locale(value: str) -> Locale | None:
    """Map ``ru``, ``ru_RU.UTF-8``, ``zh-Hans``, or ``fa-IR`` to a supported locale."""
    code = value.strip().lower().replace("-", "_").split(".")[0].split("_")[0]
    return cast(Locale, code) if code in LOCALES else None


def request_locale(locale: Locale) -> None:
    """Record ``--lang`` for this run (it wins over the environment and the saved choice)."""
    global _requested
    _requested = locale
    set_locale(locale)


def explicit_locale() -> Locale | None:
    """The language the user chose: ``--lang``, then ``MERIDIAN_LANG``, then the saved choice."""
    if _requested is not None:
        return _requested
    for value in (os.environ.get("MERIDIAN_LANG", ""), saved_locale()):
        if value and (locale := normalize_locale(value)):
            return locale
    return None


def system_locale() -> Locale:
    """The terminal's language from ``LC_ALL``/``LC_MESSAGES``/``LANG``, else English."""
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name, "")
        if value:
            return normalize_locale(value) or DEFAULT_LOCALE
    return DEFAULT_LOCALE


def saved_locale() -> str:
    """The language remembered from an earlier wizard run ('' when none)."""
    from meridian import config

    try:
        data = json.loads(config.PREFERENCES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    value = data.get("language", "") if isinstance(data, dict) else ""
    return value if isinstance(value, str) else ""


def save_locale(locale: Locale) -> None:
    """Remember the language for later runs; failing to save never blocks the wizard."""
    from meridian import config

    path = config.PREFERENCES_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["language"] = locale
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return
