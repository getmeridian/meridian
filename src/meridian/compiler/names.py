"""Deterministic names for Remnawave-owned resources."""

from __future__ import annotations

import hashlib
import re
from typing import Annotated

from pydantic import Field

REMNAWAVE_DISPLAY_NAME_PATTERN = r"^[A-Za-z0-9_ -]+$"
REMNAWAVE_USERNAME_PATTERN = r"^[A-Za-z0-9_-]+$"
REMNAWAVE_SHORT_NAME_MAX_LENGTH = 30
REMNAWAVE_TEMPLATE_NAME_MAX_LENGTH = 255
REMNAWAVE_USERNAME_MAX_LENGTH = 36

RemnawaveConfigProfileName = Annotated[
    str,
    Field(
        min_length=2,
        max_length=REMNAWAVE_SHORT_NAME_MAX_LENGTH,
        pattern=REMNAWAVE_DISPLAY_NAME_PATTERN,
    ),
]
RemnawaveNodeName = Annotated[
    str,
    Field(
        min_length=3,
        max_length=REMNAWAVE_SHORT_NAME_MAX_LENGTH,
        pattern=REMNAWAVE_DISPLAY_NAME_PATTERN,
    ),
]
RemnawaveSquadName = Annotated[
    str,
    Field(
        min_length=2,
        max_length=REMNAWAVE_SHORT_NAME_MAX_LENGTH,
        pattern=REMNAWAVE_DISPLAY_NAME_PATTERN,
    ),
]
RemnawaveTemplateName = Annotated[
    str,
    Field(
        min_length=2,
        max_length=REMNAWAVE_TEMPLATE_NAME_MAX_LENGTH,
        pattern=REMNAWAVE_DISPLAY_NAME_PATTERN,
    ),
]
RemnawaveUsername = Annotated[
    str,
    Field(
        min_length=3,
        max_length=REMNAWAVE_USERNAME_MAX_LENGTH,
        pattern=REMNAWAVE_USERNAME_PATTERN,
    ),
]


def profile_name(workload_id: str) -> str:
    """Return the stable Config Profile name for one workload."""
    return _workload_name(workload_id)


def node_name(workload_id: str) -> str:
    """Return the stable Node name for one workload."""
    return _workload_name(workload_id)


def internal_squad_name(identity: str, label: str) -> str:
    """Fit a named access-control group inside Remnawave's short-name limit."""
    return _bounded_display_name(
        label,
        identity=f"internal-squad:{identity}",
        max_length=REMNAWAVE_SHORT_NAME_MAX_LENGTH,
        force_digest=True,
    )


def external_squad_name(identity: str) -> str:
    """Return the stable external delivery-group name."""
    return _bounded_display_name(
        f"Meridian v4 {identity}",
        identity=f"external-squad:{identity}",
        max_length=REMNAWAVE_SHORT_NAME_MAX_LENGTH,
    )


def subscription_template_name(template_id: str, template_type: str) -> str:
    """Return a stable subscription-template name using the API-safe display charset."""
    label = template_type.replace("_", " ").title()
    if template_type == "XRAY_JSON":
        label = "Xray JSON"
    return _bounded_display_name(
        f"Meridian v4 {template_id} {label}",
        identity=f"subscription-template:{template_id}:{template_type}",
        max_length=REMNAWAVE_TEMPLATE_NAME_MAX_LENGTH,
    )


def service_username(edge_id: str) -> str:
    """Return a collision-resistant service username within the 36-character cap."""
    prefix = "meridian_svc_"
    digest = _digest(f"service-user:{edge_id}")
    slug = re.sub(r"[^a-z0-9_-]+", "-", edge_id.lower()).strip("_-") or "edge"
    slug_length = REMNAWAVE_USERNAME_MAX_LENGTH - len(prefix) - len(digest) - 1
    slug = slug[:slug_length].rstrip("_-") or "edge"
    return f"{prefix}{slug}_{digest}"


def _workload_name(workload_id: str) -> str:
    return _bounded_display_name(
        f"Meridian v4 {workload_id}",
        identity=f"workload:{workload_id}",
        max_length=REMNAWAVE_SHORT_NAME_MAX_LENGTH,
    )


def _bounded_display_name(
    value: str,
    *,
    identity: str,
    max_length: int,
    force_digest: bool = False,
) -> str:
    stripped = value.strip()
    normalized = re.sub(r"[^A-Za-z0-9_ -]+", "-", stripped)
    normalized = re.sub(r" +", " ", normalized).strip(" _-") or "Meridian"
    if not force_digest and normalized == stripped and len(normalized) <= max_length:
        return normalized

    digest_input = identity + "\0" + stripped
    suffix = f"-{_digest(digest_input)}"
    stem = normalized[: max_length - len(suffix)].rstrip(" _-") or "M4"
    return f"{stem}{suffix}"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
