"""Remnawave version policy: supported floor, tested major, never-downgrade images.

Operators may upgrade Remnawave themselves. Meridian installs its tested
``tag@sha256:digest`` pins on fresh servers, upgrades tags it knows are older,
and otherwise keeps whatever newer or unmanaged tag (``3``, ``latest``, a bare
digest) the operator chose.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Literal, NamedTuple

from meridian.config import (
    REMNAWAVE_BACKEND_IMAGE,
    REMNAWAVE_MIN_VERSION,
    REMNAWAVE_NODE_IMAGE,
    REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE,
    REMNAWAVE_TESTED_MAJOR,
)
from meridian.host_runtime import PANEL_DIR

from .runtime import RemnawaveError

Version = tuple[int, int, int]

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]*)?$")
_IMAGE_LINE_RE = re.compile(r"""^\s*image:\s*["']?([^\s"'#]+)""", re.MULTILINE)
_DEFAULT_REGISTRY_PREFIXES = ("docker.io/", "index.docker.io/", "registry-1.docker.io/")


def parse_version(value: str) -> Version | None:
    """Parse ``X.Y.Z`` (optionally ``v``-prefixed or with a suffix); None otherwise."""
    match = _VERSION_RE.match(value.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


class ImageRef(NamedTuple):
    """A normalized image reference: repository, tag, and content digest."""

    repository: str
    tag: str
    digest: str  # ``sha256:<hex>`` or empty

    def same_content(self, other: ImageRef) -> bool:
        """Whether both references pin the same repository and content digest."""
        return bool(self.digest) and self.repository == other.repository and self.digest == other.digest


def parse_image(image: str) -> ImageRef:
    """Split ``[registry/]repo[:tag][@digest]`` with Docker Hub prefixes normalized away."""
    reference = image.strip()
    for prefix in _DEFAULT_REGISTRY_PREFIXES:
        if reference.startswith(prefix):
            reference = reference[len(prefix) :]
            break
    reference = reference.removeprefix("library/")
    reference, _, digest = reference.partition("@")
    last_slash = reference.rfind("/")
    colon = reference.rfind(":")
    if colon > last_slash:
        return ImageRef(reference[:colon], reference[colon + 1 :], digest)
    return ImageRef(reference, "", digest)


def find_image(compose: str | None, repository: str) -> str | None:
    """Return the first ``image:`` in a compose file that uses ``repository``."""
    if not compose:
        return None
    for match in _IMAGE_LINE_RE.finditer(compose):
        if parse_image(match.group(1)).repository == repository:
            return match.group(1)
    return None


def resolve_image(pinned: str, existing: str | None) -> str:
    """Choose the image to run without ever downgrading the operator's choice.

    Versions are compared by tag; digests only identify content. The existing
    reference is kept verbatim when it already names the pinned content (any
    spelling), carries a newer version, or uses a non-version tag (``3``,
    ``latest``, a bare digest). The pin wins on fresh servers, for a different
    repository, for an older version, and for the same version so the tested
    digest replaces a tag-only or differently-digested reference.
    """
    if not existing:
        return pinned
    current = parse_image(existing)
    pin = parse_image(pinned)
    if current.repository != pin.repository:
        return pinned
    if current.same_content(pin):
        return existing
    existing_version = parse_version(current.tag)
    if existing_version is None:
        return existing
    pinned_version = parse_version(pin.tag)
    if pinned_version is None or existing_version > pinned_version:
        return existing
    return pinned


# Remnawave images `fleet upgrade` may move to a newer release in the same major
# without a Meridian release; PostgreSQL and Valkey stay Meridian-owned pins.
UPGRADABLE_PINS: dict[str, str] = {
    "remnawave_backend_image": REMNAWAVE_BACKEND_IMAGE,
    "remnawave_node_image": REMNAWAVE_NODE_IMAGE,
    "remnawave_subscription_page_image": REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE,
}


def valid_upgrade_pin(name: str, image: str) -> bool:
    """Whether a saved override names a digest-pinned release of the same repository and major."""
    default = UPGRADABLE_PINS.get(name)
    if default is None:
        return False
    ref, pin = parse_image(image), parse_image(default)
    version, pinned = parse_version(ref.tag), parse_version(pin.tag)
    return (
        ref.repository == pin.repository
        and ref.digest.startswith("sha256:")
        and version is not None
        and pinned is not None
        and version[0] == pinned[0]
    )


def effective_pins(overrides: Mapping[str, str]) -> dict[str, str]:
    """This CLI's Remnawave pins, raised to a newer release `fleet upgrade` saved.

    A saved override older than (or equal to) the CLI's pin yields to it, so
    updating Meridian never moves a fleet backwards.
    """
    pins = dict(UPGRADABLE_PINS)
    for name, image in overrides.items():
        if not valid_upgrade_pin(name, image):
            continue
        saved = parse_version(parse_image(image).tag)
        default = parse_version(parse_image(pins[name]).tag)
        if saved is not None and default is not None and saved > default:
            pins[name] = image
    return pins


_DATA_TAG_RE = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:-.*)?$")


def resolve_data_image(pinned: str, existing: str | None, *, cross_major: bool = True) -> str:
    """Choose a data-store image (PostgreSQL, Valkey) without moving data backwards.

    Their tags look like ``17.11-alpine`` rather than X.Y.Z, and an older major
    cannot open a newer major's data directory. Keep the existing reference
    when it already names the pinned content, carries a newer numeric tag, or
    has a non-numeric tag Meridian cannot order (``latest``); otherwise pin.
    With ``cross_major=False`` (PostgreSQL, whose data directory needs a dump
    and restore between majors) an older major is kept too.
    """
    if not existing:
        return pinned
    current = parse_image(existing)
    pin = parse_image(pinned)
    if current.repository != pin.repository:
        return pinned
    if current.same_content(pin):
        return existing
    current_match = _DATA_TAG_RE.match(current.tag)
    pin_match = _DATA_TAG_RE.match(pin.tag)
    if current_match is None or pin_match is None:
        return existing
    current_version = tuple(int(part or 0) for part in current_match.groups())
    pinned_version = tuple(int(part or 0) for part in pin_match.groups())
    if current_version > pinned_version:
        return existing
    if not cross_major and current_version[0] != pinned_version[0]:
        return existing
    return pinned


ImageStatus = Literal["current", "upgrade", "absent", "kept_newer", "kept_unmanaged", "held_major"]


def image_status(pinned: str, running: str | None, *, data: bool = False, cross_major: bool = True) -> ImageStatus:
    """Classify a running image against its pin with the same policy setup applies.

    ``upgrade`` and ``absent`` are what an apply would change; ``kept_*`` are
    operator choices the policy leaves alone; ``held_major`` is a PostgreSQL
    major the pin moved past, which needs a manual dump and restore.
    """
    if not running:
        return "absent"
    if data:
        chosen = resolve_data_image(pinned, running, cross_major=cross_major)
        if chosen == running and resolve_data_image(pinned, running) != running:
            return "held_major"
    else:
        chosen = resolve_image(pinned, running)
    if chosen != running:
        return "upgrade"
    current, pin = parse_image(running), parse_image(pinned)
    if running == pinned or current.same_content(pin):
        return "current"
    if data:
        match = _DATA_TAG_RE.match(current.tag)
        return "kept_newer" if match is not None else "kept_unmanaged"
    return "kept_newer" if parse_version(current.tag) is not None else "kept_unmanaged"


def data_major(image: str) -> int | None:
    """The leading numeric tag component of a data-store image (``17`` for ``postgres:17.11-alpine``)."""
    match = _DATA_TAG_RE.match(parse_image(image).tag)
    return int(match.group(1)) if match is not None else None


def effective_image(pinned: str, compose: str | None) -> str:
    """Resolve the image for ``pinned``'s repository against an existing compose file."""
    return resolve_image(pinned, find_image(compose, parse_image(pinned).repository))


def image_below_floor(image: str | None) -> bool:
    """Return whether ``image`` carries a version tag older than the supported floor."""
    if not image:
        return False
    version = parse_version(parse_image(image).tag)
    floor = parse_version(REMNAWAVE_MIN_VERSION)
    return version is not None and floor is not None and version < floor


def check_panel_version(version: str) -> str:
    """Refuse a panel below the floor; return a warning for an untested newer major.

    Returns an empty string when the version is inside the tested range.
    Unparseable versions (dev builds) are accepted with a warning.
    """
    parsed = parse_version(version)
    floor = parse_version(REMNAWAVE_MIN_VERSION)
    if parsed is None:
        return (
            f"Remnawave reported an unrecognized version {version!r}; "
            f"Meridian is tested with {REMNAWAVE_TESTED_MAJOR}.x."
        )
    if floor is not None and parsed < floor:
        raise RemnawaveError(
            f"Remnawave {version} is older than the minimum supported {REMNAWAVE_MIN_VERSION}",
            hint=f"Upgrade the panel (re-run setup, or update the image tags in {PANEL_DIR}) and retry.",
            category="user",
        )
    if parsed[0] > REMNAWAVE_TESTED_MAJOR:
        return (
            f"Remnawave {version} is newer than the tested {REMNAWAVE_TESTED_MAJOR}.x line; "
            "continuing, but report any API errors."
        )
    return ""
