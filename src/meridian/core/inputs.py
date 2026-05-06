"""Reusable validated input types for Meridian request models."""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import AfterValidator, Field, IPvAnyAddress, TypeAdapter, ValidationError, WithJsonSchema

_IP_ADDRESS: TypeAdapter[Any] = TypeAdapter(IPvAnyAddress)
_IP_ADDRESS_SCHEMA = {
    "anyOf": [{"type": "string", "format": "ipv4"}, {"type": "string", "format": "ipv6"}],
}
_DEPLOY_IP_SCHEMA = {
    "anyOf": [
        {"type": "string", "format": "ipv4"},
        {"type": "string", "format": "ipv6"},
        {"type": "string", "pattern": r"^$|^[Ll][Oo][Cc][Aa][Ll](?:[Ll][Yy])?$"},
    ],
}
_NAME_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"
_OPTIONAL_NAME_PATTERN = rf"^$|{_NAME_PATTERN}"
_SSH_USER_PATTERN = r"^[a-zA-Z0-9._-]+$"
_OPTIONAL_SSH_USER_PATTERN = rf"^$|{_SSH_USER_PATTERN}"
_SELECTOR_PATTERN = r"^\S+$"
_OPTIONAL_SELECTOR_PATTERN = r"^\S*$"
_NAME_RE = re.compile(_NAME_PATTERN)
_SSH_USER_RE = re.compile(_SSH_USER_PATTERN)
_LOCAL_TARGETS = {"local", "locally"}
_SERVER_TITLE_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 80, "pattern": r"^[^\r\n\t]+$"}
_SERVER_REF_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 120, "pattern": r"^[^\r\n\t]+$"}
_OPTIONAL_SERVER_REF_SCHEMA = {"type": "string", "maxLength": 120, "pattern": r"^$|^[^\r\n\t]+$"}


def is_local_deploy_target(value: str) -> bool:
    """Return True for deploy targets that mean the current machine."""
    return value.lower() in _LOCAL_TARGETS


def is_ip_deploy_target(value: str) -> bool:
    """Return True when a deploy target is a valid IP address."""
    try:
        _IP_ADDRESS.validate_python(value)
        return True
    except ValidationError:
        return False


def validate_ip_address_value(value: str) -> str:
    """Validate a required IPv4/IPv6 address while preserving string output."""
    if is_ip_deploy_target(value):
        return value
    raise ValueError("Enter a valid IP address.")


def validate_deploy_ip_value(value: str) -> str:
    """Validate the explicit deploy target field."""
    if not value:
        return value
    if is_local_deploy_target(value):
        return value.lower()
    if is_ip_deploy_target(value):
        return value
    raise ValueError("Enter a valid IP address, or use 'local' when running Meridian on the target server.")


def validate_selector_value(value: str) -> str:
    """Validate a required name/IP selector."""
    if not value:
        raise ValueError("A name or IP address is required.")
    return validate_optional_selector_value(value)


def validate_optional_selector_value(value: str) -> str:
    """Validate an optional name/IP selector without checking existence."""
    if not value:
        return value
    if value.strip() != value or any(char.isspace() for char in value):
        raise ValueError("Names and IP addresses cannot contain spaces.")
    if is_local_deploy_target(value):
        return value.lower()
    return value


def validate_ssh_user_value(value: str) -> str:
    """Validate SSH usernames before runtime adapters build a connection."""
    if not value:
        raise ValueError("SSH user is required.")
    if not _SSH_USER_RE.match(value):
        raise ValueError("Use letters, numbers, dots, hyphens, and underscores.")
    return value


def validate_optional_ssh_user_value(value: str) -> str:
    """Validate an optional SSH user field where empty means fallback."""
    if not value:
        return value
    return validate_ssh_user_value(value)


def validate_name_value(value: str) -> str:
    """Validate an optional stable display/resource name."""
    if not value:
        return value
    if not _NAME_RE.match(value):
        raise ValueError("Use letters, numbers, hyphens, and underscores.")
    return value


def validate_required_name_value(value: str) -> str:
    """Validate a required stable display/resource name."""
    if not value:
        raise ValueError("Name is required.")
    return validate_name_value(value)


def validate_server_title_value(value: str) -> str:
    """Validate a human display title without forcing storage-shaped slugs."""
    value = value.strip()
    if not value:
        raise ValueError("Server title is required.")
    if len(value) > 80:
        raise ValueError("Server title must be 80 characters or fewer.")
    if any(char in value for char in "\r\n\t"):
        raise ValueError("Server title cannot contain tabs or line breaks.")
    return value


def validate_server_reference_value(value: str) -> str:
    """Validate a title/IP/ID reference entered by a human."""
    value = value.strip()
    if not value:
        raise ValueError("Choose a saved server or enter a server reference.")
    if len(value) > 120 or any(char in value for char in "\r\n\t"):
        raise ValueError("Server references cannot contain tabs or line breaks.")
    return value


def validate_optional_server_reference_value(value: str) -> str:
    """Validate an optional human server reference."""
    if not value:
        return value
    return validate_server_reference_value(value)


IPAddressValue = Annotated[
    str,
    WithJsonSchema(_IP_ADDRESS_SCHEMA),
    AfterValidator(validate_ip_address_value),
]
DeployIpValue = Annotated[
    str,
    WithJsonSchema(_DEPLOY_IP_SCHEMA),
    AfterValidator(validate_deploy_ip_value),
]
SelectorValue = Annotated[
    str,
    WithJsonSchema({"type": "string", "pattern": _SELECTOR_PATTERN, "minLength": 1}),
    AfterValidator(validate_selector_value),
]
OptionalSelectorValue = Annotated[
    str,
    WithJsonSchema({"type": "string", "pattern": _OPTIONAL_SELECTOR_PATTERN}),
    AfterValidator(validate_optional_selector_value),
]
SshUserValue = Annotated[
    str,
    WithJsonSchema({"type": "string", "pattern": _SSH_USER_PATTERN, "minLength": 1}),
    AfterValidator(validate_ssh_user_value),
]
OptionalSshUserValue = Annotated[
    str,
    WithJsonSchema({"type": "string", "pattern": _OPTIONAL_SSH_USER_PATTERN}),
    AfterValidator(validate_optional_ssh_user_value),
]
NameValue = Annotated[
    str,
    WithJsonSchema({"type": "string", "pattern": _OPTIONAL_NAME_PATTERN}),
    AfterValidator(validate_name_value),
]
RequiredNameValue = Annotated[
    str,
    WithJsonSchema({"type": "string", "pattern": _NAME_PATTERN, "minLength": 1}),
    AfterValidator(validate_required_name_value),
]
ServerTitleValue = Annotated[
    str,
    WithJsonSchema(_SERVER_TITLE_SCHEMA),
    AfterValidator(validate_server_title_value),
]
ServerReferenceValue = Annotated[
    str,
    WithJsonSchema(_SERVER_REF_SCHEMA),
    AfterValidator(validate_server_reference_value),
]
OptionalServerReferenceValue = Annotated[
    str,
    WithJsonSchema(_OPTIONAL_SERVER_REF_SCHEMA),
    AfterValidator(validate_optional_server_reference_value),
]
PortValue = Annotated[int, Field(ge=1, le=65535)]
