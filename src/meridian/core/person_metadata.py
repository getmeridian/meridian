"""Bounded, operator-owned person metadata; never part of connection delivery."""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated

from pydantic import AfterValidator, Field

from meridian.core.errors import MeridianError

MAX_NOTE_CHARACTERS = 500
MAX_TAG_CHARACTERS = 16
PERSON_TAG_PATTERN = r"^[A-Z0-9_]{0,16}$"


def validate_private_note(value: str) -> str:
    """Keep plain text verbatim, allowing line feeds and refusing control characters."""
    if len(value) > MAX_NOTE_CHARACTERS or any(
        character != "\n" and unicodedata.category(character) in {"Cc", "Cs"} for character in value
    ):
        raise ValueError("Use a plain-text note of at most 500 characters; newlines are allowed.")
    return value


def validate_person_tag(value: str) -> str:
    """One uppercase tag, or the empty string to clear it."""
    if not re.fullmatch(PERSON_TAG_PATTERN, value):
        raise ValueError("Use 1–16 uppercase letters, digits, or underscores, or clear the tag.")
    return value


PrivateNote = Annotated[str, AfterValidator(validate_private_note), Field(max_length=MAX_NOTE_CHARACTERS)]
PersonTag = Annotated[
    str, AfterValidator(validate_person_tag), Field(max_length=MAX_TAG_CHARACTERS, pattern=PERSON_TAG_PATTERN)
]


class PersonMetadataError(MeridianError):
    """Invalid private metadata, with its field identified but no private value in the error."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message, category="user")
        self.field = field


def parse_note_option(value: str) -> str:
    try:
        return validate_private_note(value)
    except ValueError as exc:
        raise PersonMetadataError("note", str(exc)) from exc


def parse_tag_option(value: str) -> str:
    """The CLI's explicit `none` clears a tag; any other spelling is uppercased, so `NONE` remains a tag."""
    try:
        return validate_person_tag("" if value == "none" else value.upper())
    except ValueError as exc:
        raise PersonMetadataError("tag", str(exc)) from exc
