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
_TOPOLOGY_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"
_OPTIONAL_TOPOLOGY_ID_PATTERN = rf"^$|{_TOPOLOGY_ID_PATTERN}"
_ACCESS_USERNAME_PATTERN = r"^[A-Za-z0-9_-]+$"
_SSH_USER_PATTERN = r"^[a-zA-Z0-9._-]+$"
_OPTIONAL_SSH_USER_PATTERN = rf"^$|{_SSH_USER_PATTERN}"
_SELECTOR_PATTERN = r"^\S+$"
_OPTIONAL_SELECTOR_PATTERN = r"^\S*$"
_NAME_RE = re.compile(_NAME_PATTERN)
_TOPOLOGY_ID_RE = re.compile(_TOPOLOGY_ID_PATTERN)
_ACCESS_USERNAME_RE = re.compile(_ACCESS_USERNAME_PATTERN)
_SSH_USER_RE = re.compile(_SSH_USER_PATTERN)
_LOCAL_TARGETS = {"local", "locally"}
TOPOLOGY_ID_MAX_LENGTH = 48
_SERVER_TITLE_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 80, "pattern": r"^[^\r\n\t]+$"}
_SERVER_REF_SCHEMA = {"type": "string", "minLength": 1, "maxLength": 120, "pattern": r"^[^\r\n\t]+$"}
_OPTIONAL_SERVER_REF_SCHEMA = {"type": "string", "maxLength": 120, "pattern": r"^$|^[^\r\n\t]+$"}
_COUNTRY_CODE_SCHEMA = {"type": "string", "minLength": 2, "maxLength": 2, "pattern": r"^[A-Za-z]{2}$"}
_OPTIONAL_COUNTRY_CODE_SCHEMA = {"type": "string", "maxLength": 2, "pattern": r"^$|^[A-Za-z]{2}$"}
_HOSTNAME_SCHEMA = {
    "type": "string",
    "minLength": 1,
    "maxLength": 253,
    "pattern": (
        r"^(?=.{1,253}\.?$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
        r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$"
    ),
}
_OPTIONAL_HOSTNAME_SCHEMA = {"anyOf": [{"type": "string", "const": ""}, _HOSTNAME_SCHEMA]}
_TRANSPORT_PATH_SCHEMA = {
    "type": "string",
    "maxLength": 200,
    "pattern": r"^$|^/?[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*$",
}


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


def validate_topology_id_value(value: str) -> str:
    """Validate one stable lowercase topology identifier."""
    if not value:
        raise ValueError("Topology ID is required.")
    if len(value) > TOPOLOGY_ID_MAX_LENGTH:
        raise ValueError(f"Topology IDs must be {TOPOLOGY_ID_MAX_LENGTH} characters or fewer.")
    if _TOPOLOGY_ID_RE.fullmatch(value) is None:
        raise ValueError("Use lowercase letters, numbers, hyphens, and underscores for topology IDs.")
    return value


def validate_optional_topology_id_value(value: str) -> str:
    """Validate an optional topology identifier."""
    if not value:
        return value
    return validate_topology_id_value(value)


def validate_access_username_value(value: str) -> str:
    """Validate a Remnawave access username at the public intent boundary."""
    if len(value) < 3 or len(value) > 36:
        raise ValueError("Access usernames must be between 3 and 36 characters.")
    if _ACCESS_USERNAME_RE.fullmatch(value) is None:
        raise ValueError("Access usernames can use only letters, numbers, hyphens, and underscores.")
    return value


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


def validate_country_code_value(value: str) -> str:
    """Validate an ISO-3166 alpha-2 country code and normalize to uppercase."""
    normalized = value.strip().upper()
    if len(normalized) != 2 or not normalized.isalpha() or not normalized.isascii():
        raise ValueError("Use a two-letter country code such as RU.")
    return normalized


def validate_optional_country_code_value(value: str) -> str:
    """Validate an optional ISO-3166 alpha-2 country code."""
    if not value:
        return value
    return validate_country_code_value(value)


def validate_hostname_value(value: str) -> str:
    """Validate and canonicalize a DNS hostname used for TLS or Reality SNI."""
    normalized = value.strip().rstrip(".")
    if not normalized:
        raise ValueError("Enter a valid domain name.")
    try:
        ascii_name = normalized.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("Enter a valid domain name.") from exc
    if len(ascii_name) > 253:
        raise ValueError("Domain names must be 253 characters or fewer.")
    labels = ascii_name.split(".")
    if len(labels) < 2:
        raise ValueError("Enter a full domain name such as vpn.example.com.")
    for label in labels:
        if (
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or re.fullmatch(r"[a-z0-9-]+", label) is None
        ):
            raise ValueError("Enter a valid domain name.")
    return ascii_name


def validate_optional_hostname_value(value: str) -> str:
    """Validate an optional DNS hostname."""
    if not value:
        return value
    return validate_hostname_value(value)


def validate_optional_transport_path_value(value: str) -> str:
    """Validate a relative XHTTP/WebSocket path before nginx rendering."""
    normalized = value.strip().lstrip("/")
    if not normalized:
        return ""
    if len(normalized) > 200:
        raise ValueError("Transport paths must be 200 characters or fewer.")
    parts = normalized.split("/")
    if any(part in {".", ".."} or re.fullmatch(r"[A-Za-z0-9._~-]+", part) is None for part in parts):
        raise ValueError("Use only URL-safe path segments without spaces or traversal.")
    return normalized


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
TopologyIdValue = Annotated[
    str,
    WithJsonSchema(
        {
            "type": "string",
            "pattern": _TOPOLOGY_ID_PATTERN,
            "minLength": 1,
            "maxLength": TOPOLOGY_ID_MAX_LENGTH,
        }
    ),
    AfterValidator(validate_topology_id_value),
]
OptionalTopologyIdValue = Annotated[
    str,
    WithJsonSchema(
        {
            "type": "string",
            "pattern": _OPTIONAL_TOPOLOGY_ID_PATTERN,
            "maxLength": TOPOLOGY_ID_MAX_LENGTH,
        }
    ),
    AfterValidator(validate_optional_topology_id_value),
]
AccessUsernameValue = Annotated[
    str,
    WithJsonSchema(
        {
            "type": "string",
            "pattern": _ACCESS_USERNAME_PATTERN,
            "minLength": 3,
            "maxLength": 36,
        }
    ),
    AfterValidator(validate_access_username_value),
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
CountryCodeValue = Annotated[
    str,
    WithJsonSchema(_COUNTRY_CODE_SCHEMA),
    AfterValidator(validate_country_code_value),
]
OptionalCountryCodeValue = Annotated[
    str,
    WithJsonSchema(_OPTIONAL_COUNTRY_CODE_SCHEMA),
    AfterValidator(validate_optional_country_code_value),
]
HostnameValue = Annotated[
    str,
    WithJsonSchema(_HOSTNAME_SCHEMA),
    AfterValidator(validate_hostname_value),
]
OptionalHostnameValue = Annotated[
    str,
    WithJsonSchema(_OPTIONAL_HOSTNAME_SCHEMA),
    AfterValidator(validate_optional_hostname_value),
]
OptionalTransportPathValue = Annotated[
    str,
    WithJsonSchema(_TRANSPORT_PATH_SCHEMA),
    AfterValidator(validate_optional_transport_path_value),
]
PortValue = Annotated[int, Field(ge=1, le=65535)]
