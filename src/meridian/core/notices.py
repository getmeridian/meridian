"""Bounded operator notices and the shared preview/confirmation contract."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, Field, WithJsonSchema

from meridian.core.agent_types import Timestamp
from meridian.core.models import CoreModel

NOTICE_MAX_LENGTH = 500
NOTICE_RENDERED_MAX_LENGTH = 1024
NOTICE_FIELDS = ("name", "expires", "days_left", "used", "quota")
_FIELD = re.compile(r"\{(name|expires|days_left|used|quota)\}")
_VALUE_BOUNDS = {"name": 64, "expires": 24, "days_left": 8, "used": 24, "quota": 24}


class NoticeTextError(ValueError):
    """A small error code lets CLI adapters translate the validation without parsing prose."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_notice_text(text: str) -> str:
    """Only literal text and whole fixed placeholders; no formatting or attribute access."""
    if not text.strip() or len(text) > NOTICE_MAX_LENGTH:
        raise NoticeTextError("length", f"Use 1–{NOTICE_MAX_LENGTH} characters of plain text.")
    if any(unicodedata.category(char).startswith("C") and char not in "\u200c\u200d" for char in text):
        raise NoticeTextError("paragraph", "Use a single paragraph without control characters.")
    if any(char in text for char in "\u2028\u2029"):
        raise NoticeTextError("paragraph", "Use a single paragraph without line breaks.")
    literal = _FIELD.sub("", text)
    if "{" in literal or "}" in literal:
        allowed = ", ".join("{" + name + "}" for name in NOTICE_FIELDS)
        raise NoticeTextError("substitutions", f"Allowed substitutions: {allowed}.")
    expanded = _FIELD.sub(lambda match: "x" * _VALUE_BOUNDS[match[1]], text)
    if len(expanded) > NOTICE_RENDERED_MAX_LENGTH:
        raise NoticeTextError(
            "expansion", f"Use fewer substitutions so the notice fits within {NOTICE_RENDERED_MAX_LENGTH} characters."
        )
    return text


NoticeText = Annotated[
    str,
    AfterValidator(validate_notice_text),
    WithJsonSchema({"type": "string", "minLength": 1, "maxLength": NOTICE_MAX_LENGTH}),
]


class Notice(CoreModel):
    """One service-owned notice; reviewed topology applies never replace it."""

    text: NoticeText = Field(description="One plain paragraph; only {name}, {expires}, {days_left}, {used}, {quota}.")
    until: Timestamp | None = Field(default=None, description="UTC expiry instant; null keeps the notice indefinitely.")


class RenderedNotice(CoreModel):
    """The current person's literal notice, never a template or a credential."""

    text: str = Field(min_length=1, max_length=NOTICE_RENDERED_MAX_LENGTH)
    until: Timestamp | None = None


class NoticeWrite(CoreModel):
    """The same operation previews (confirm=false) or saves (confirm=true); null clears."""

    notice: Notice | None
    for_user: str | None = Field(default=None, min_length=1, max_length=64)
    confirm: bool = False


class NoticeState(CoreModel):
    notice: Notice | None
    active: bool
    for_user: str | None
    preview: str = Field(max_length=NOTICE_RENDERED_MAX_LENGTH)
    changed: bool = False
    allowed_substitutions: list[str] = Field(default_factory=lambda: list(NOTICE_FIELDS))


def substitute_notice(text: str, values: dict[str, str]) -> str:
    """Substitute once: braces in a person's name or a value remain literal."""
    return _FIELD.sub(lambda match: values[match[1]], text)


def parse_notice_until(text: str, *, now: datetime | None = None) -> str:
    """A future ISO instant, or the end of a UTC calendar date, without account-expiry sentinels."""
    value = text.strip()
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        moment = datetime.fromisoformat(value + "T23:59:59+00:00")
    else:
        moment = datetime.fromisoformat(value)
    if moment.tzinfo is None or moment <= (now or datetime.now(UTC)):
        raise ValueError("Use a future date or ISO time with a UTC offset.")
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
