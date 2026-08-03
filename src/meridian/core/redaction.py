"""Secret redaction helpers for meridian-core JSON and diagnostics."""

from __future__ import annotations

import re
from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel

REDACTED = "[redacted]"

_SENSITIVE_KEY_PARTS = (
    "api_token",
    "apikey",
    "api_key",
    "secretkey",
    "secret_key",
    "access_token",
    "accesstoken",
    "refresh_token",
    "refreshtoken",
    "auth_token",
    "authtoken",
    "jwt",
    "password",
    "passwd",
    "secret",
    "private_key",
    "privatekey",
    "database_url",
    "databaseurl",
    "db_url",
    "dburl",
    "subscription_secret",
    "subscription_url",
    "subscriptionurl",
    "sub_url",
    "suburl",
    "share_url",
    "shareurl",
    "access_link",
    "accesslink",
    "connection_url",
    "connectionurl",
)
_SENSITIVE_EXACT_KEYS = {
    "token",
    "session_token",
    "sessiontoken",
    "id_token",
    "idtoken",
}

_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_PEM_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_AUTH_HEADER_RE = re.compile(
    r"\b(?P<key>Authorization\"?\s*:\s*\"?(?:Bearer\s+)?)(?P<value>[^\"'\s,;`]+)",
    re.I,
)
_API_KEY_HEADER_RE = re.compile(
    r"\b(?P<key>(?:X-API-Key|API-Key)\"?\s*:\s*\"?)(?P<value>[^\"'\s,;`]+)",
    re.I,
)
_URL_USERINFO_RE = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<userinfo>[^/@\s]+)@(?P<host>[^/\s]+)", re.I)
_HTTP_URL_PATH_RE = re.compile(
    r"(?P<scheme>https?://)(?P<host>[^/\s?#]+)/(?P<secret>[^/\s?#,;'\"`][^\s?#,;'\"`]*)(?P<suffix>[?#][^\s,;'\"`]*)?",
    re.I,
)
_ASSIGNMENT_RE = re.compile(
    r"(?P<key>\b(?:api[_-]?key|api[_-]?token|access[_-]?token|refresh[_-]?token|"
    r"auth[_-]?token|session[_-]?token|id[_-]?token|token|jwt|password|passwd|"
    r"secret(?:[_-]?key)?|private[_-]?key|"
    r"database[_-]?url|db[_-]?url)\b)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<value>[^\s,;]+)",
    re.I,
)
_JSON_SECRET_PAIR_RE = re.compile(
    r"(?P<key>[\"']?(?:api[_-]?key|api[_-]?token|access[_-]?token|refresh[_-]?token|"
    r"auth[_-]?token|session[_-]?token|id[_-]?token|token|jwt|password|passwd|"
    r"secret(?:[_-]?key)?|private[_-]?key|"
    r"database[_-]?url|db[_-]?url|subscription[_-]?url|sub[_-]?url|share[_-]?url|"
    r"access[_-]?link|connection[_-]?url)[\"']?\s*:\s*)"
    r"(?P<quote>[\"']?)(?P<value>[^\"'\s,;}]+)(?P=quote)",
    re.I,
)
_JSON_SCHEMA_KEYS = {
    "$ref",
    "additionalProperties",
    "allOf",
    "anyOf",
    "const",
    "description",
    "enum",
    "format",
    "items",
    "maxLength",
    "minLength",
    "oneOf",
    "properties",
    "title",
    "type",
}


def is_sensitive_key(key: str) -> bool:
    """Return True when a mapping key should never expose its value."""
    normalized = key.lower().replace("-", "_")
    compact = normalized.replace("_", "")
    if normalized in _SENSITIVE_EXACT_KEYS or compact in _SENSITIVE_EXACT_KEYS:
        return True
    return any(part in normalized or part in compact for part in _SENSITIVE_KEY_PARTS)


def redact_string(value: str) -> str:
    """Redact common secret forms inside free-form text."""
    redacted = _PEM_RE.sub(REDACTED, value)
    redacted = _AUTH_HEADER_RE.sub(lambda m: f"{m.group('key')}{REDACTED}", redacted)
    redacted = _API_KEY_HEADER_RE.sub(lambda m: f"{m.group('key')}{REDACTED}", redacted)
    redacted = _JWT_RE.sub(REDACTED, redacted)
    redacted = _URL_USERINFO_RE.sub(lambda m: f"{m.group('scheme')}{REDACTED}@{m.group('host')}", redacted)
    redacted = _HTTP_URL_PATH_RE.sub(lambda m: f"{m.group('scheme')}{m.group('host')}/{REDACTED}", redacted)
    redacted = _JSON_SECRET_PAIR_RE.sub(
        lambda m: f"{m.group('key')}{m.group('quote')}{REDACTED}{m.group('quote')}",
        redacted,
    )
    return _ASSIGNMENT_RE.sub(lambda m: f"{m.group('key')}{m.group('sep')}{REDACTED}", redacted)


def _is_json_schema_property_map(value: dict[Any, Any]) -> bool:
    """Return True for JSON Schema properties maps, not arbitrary property bags."""
    if not value:
        return False
    for schema in value.values():
        if not isinstance(schema, dict):
            return False
        if not any(str(key) in _JSON_SCHEMA_KEYS for key in schema):
            return False
    return True


def _is_safe_metadata_key(key: str, value: Any) -> bool:
    """Return True for fields whose names look sensitive but values are metadata."""
    return key.lower().replace("-", "_") == "secret" and isinstance(value, bool)


def redact(value: Any) -> Any:
    """Return a JSON-safe structure with obvious secrets removed."""
    if isinstance(value, BaseModel):
        return redact(value.model_dump(mode="json", by_alias=True))
    if is_dataclass(value):
        return {field.name: redact(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if key_str == "properties" and isinstance(item, dict) and _is_json_schema_property_map(item):
                redacted[key_str] = {str(prop): redact(schema) for prop, schema in item.items()}
            else:
                redacted[key_str] = (
                    REDACTED if is_sensitive_key(key_str) and not _is_safe_metadata_key(key_str, item) else redact(item)
                )
        return redacted
    if isinstance(value, list | tuple | set | frozenset):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_string(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    return redact_string(str(value))
