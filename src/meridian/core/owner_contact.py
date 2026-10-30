"""Public owner contact, chosen in reviewed delivery intent."""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from typing import Annotated
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from pydantic import AfterValidator, Field, WithJsonSchema

from meridian.core.inputs import validate_hostname_value
from meridian.core.models import CoreModel

MAX_CONTACT_TEXT_CHARACTERS = 120
MAX_CONTACT_LINK_CHARACTERS = 2048
_LINK_ERROR = "Use an absolute https:, tg:, or mailto: link without credentials or control characters."
_LINK_SCHEMA = {
    "type": "string",
    "maxLength": MAX_CONTACT_LINK_CHARACTERS,
    "pattern": r"^(?:|https://[^\s]+|tg://[^\s]+|mailto:[^\s]+)$",
}


def contact_text(value: str) -> str:
    """A short single line of plain text, with surrounding whitespace removed."""
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
        raise ValueError("Use one line of plain text, up to 120 characters.")
    text = value.strip()
    if len(text) > MAX_CONTACT_TEXT_CHARACTERS:
        raise ValueError("Use one line of plain text, up to 120 characters.")
    return text


def contact_link(value: str) -> str:
    """Canonical ASCII URI for a user-initiated link; validation performs no network lookup."""
    text = value.strip()
    if not text:
        return ""
    try:
        decoded = unquote(text, errors="strict")
        if (
            len(text) > MAX_CONTACT_LINK_CHARACTERS
            or "\\" in text
            or re.search(r"%(?![0-9A-Fa-f]{2})", text)
            or any(character.isspace() or unicodedata.category(character) in {"Cc", "Cs"} for character in text)
            or any(unicodedata.category(character) in {"Cc", "Cs"} for character in decoded)
        ):
            raise ValueError(_LINK_ERROR)
        parsed = urlsplit(text)
        scheme = parsed.scheme.lower()
        if scheme == "https":
            if not parsed.hostname or "@" in parsed.netloc:
                raise ValueError(_LINK_ERROR)
            host = _host(parsed.hostname)
            port = parsed.port
            if port is not None and not 1 <= port <= 65535:
                raise ValueError(_LINK_ERROR)
            authority = f"[{host}]" if ":" in host else host
            if port is not None and port != 443:
                authority += f":{port}"
            result = urlunsplit(
                (
                    scheme,
                    authority,
                    _component(parsed.path or "/"),
                    _component(parsed.query),
                    _component(parsed.fragment),
                )
            )
        elif scheme == "tg":
            action = parsed.netloc or parsed.path
            if (
                not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", action)
                or parsed.netloc
                and parsed.path not in ("", "/")
            ):
                raise ValueError(_LINK_ERROR)
            result = urlunsplit((scheme, action.lower(), "", _component(parsed.query), _component(parsed.fragment)))
        elif scheme == "mailto":
            address = unquote(parsed.path, errors="strict")
            local, separator, domain = address.rpartition("@")
            if parsed.netloc or parsed.fragment or not separator or not _mail_local(local):
                raise ValueError(_LINK_ERROR)
            result = "mailto:" + quote(local, safe="!$&'()*+-._~") + "@" + _host(domain)
            if parsed.query:
                result += "?" + _component(parsed.query)
        else:
            raise ValueError(_LINK_ERROR)
        if not result.isascii() or len(result) > MAX_CONTACT_LINK_CHARACTERS:
            raise ValueError(_LINK_ERROR)
        return result
    except (ValueError, UnicodeError) as exc:
        raise ValueError(_LINK_ERROR) from exc


def _host(value: str) -> str:
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        return validate_hostname_value(value)


def _mail_local(value: str) -> bool:
    return (
        bool(value)
        and not (value.startswith(".") or value.endswith(".") or ".." in value)
        and all(character.isalnum() or character in ".!#$%&'*+-/=?^_`{|}~" for character in value)
    )


def _component(value: str) -> str:
    encoded = quote(value, safe="/?:@!$&'()*+,;=-._~%")
    return re.sub(r"%[0-9A-Fa-f]{2}", lambda match: match[0].upper(), encoded)


ContactText = Annotated[
    str,
    AfterValidator(contact_text),
    WithJsonSchema(
        {"type": "string", "maxLength": MAX_CONTACT_TEXT_CHARACTERS, "pattern": r"^[^\x00-\x1f\x7f-\x9f]*$"}
    ),
]
ContactLink = Annotated[str, AfterValidator(contact_link), WithJsonSchema(_LINK_SCHEMA)]


class OwnerContact(CoreModel):
    text: ContactText = ""
    link: ContactLink = ""

    @property
    def is_empty(self) -> bool:
        return not self.text and not self.link


class OwnerContactSettings(CoreModel):
    owner_contact: OwnerContact = Field(
        default_factory=OwnerContact,
        exclude_if=lambda value: value.is_empty,
        description="Optional public owner contact, chosen through reviewed delivery intent.",
    )
