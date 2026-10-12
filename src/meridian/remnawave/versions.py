"""Remnawave version policy: supported floor, tested major, never-downgrade images.

Operators may upgrade Remnawave themselves. Meridian installs its tested
image tags on fresh servers, upgrades tags it knows are older, and otherwise
keeps whatever newer or unmanaged tag (``3``, ``latest``, a digest) the
operator chose.
"""

from __future__ import annotations

import re

from meridian.config import REMNAWAVE_MIN_VERSION, REMNAWAVE_TESTED_MAJOR

from .runtime import RemnawaveError

Version = tuple[int, int, int]

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]*)?$")
_IMAGE_LINE_RE = re.compile(r"""^\s*image:\s*["']?([^\s"'#]+)""", re.MULTILINE)
_DEFAULT_REGISTRY_PREFIXES = ("docker.io/library/", "docker.io/", "index.docker.io/")


def parse_version(value: str) -> Version | None:
    """Parse ``X.Y.Z`` (optionally ``v``-prefixed or with a suffix); None otherwise."""
    match = _VERSION_RE.match(value.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def split_image(image: str) -> tuple[str, str]:
    """Split an image reference into a normalized repository and its tag or digest."""
    reference = image.strip()
    for prefix in _DEFAULT_REGISTRY_PREFIXES:
        if reference.startswith(prefix):
            reference = reference[len(prefix) :]
            break
    if "@" in reference:
        repo, _, digest = reference.partition("@")
        return repo, "@" + digest
    last_slash = reference.rfind("/")
    colon = reference.rfind(":")
    if colon > last_slash:
        return reference[:colon], reference[colon + 1 :]
    return reference, ""


def find_image(compose: str | None, repository: str) -> str | None:
    """Return the first ``image:`` in a compose file that uses ``repository``."""
    if not compose:
        return None
    for match in _IMAGE_LINE_RE.finditer(compose):
        if split_image(match.group(1))[0] == repository:
            return match.group(1)
    return None


def resolve_image(pinned: str, existing: str | None) -> str:
    """Choose the image to run without ever downgrading the operator's choice.

    The pinned tag wins only on fresh servers, for a different repository, or
    when the existing tag is a version older than the pin. Newer versions and
    non-version tags are user-managed and kept verbatim.
    """
    if not existing:
        return pinned
    repo, tag = split_image(existing)
    pinned_repo, pinned_tag = split_image(pinned)
    if repo != pinned_repo:
        return pinned
    existing_version = parse_version(tag)
    if existing_version is None:
        return existing
    pinned_version = parse_version(pinned_tag)
    if pinned_version is None or existing_version >= pinned_version:
        return existing
    return pinned


def effective_image(pinned: str, compose: str | None) -> str:
    """Resolve the image for ``pinned``'s repository against an existing compose file."""
    return resolve_image(pinned, find_image(compose, split_image(pinned)[0]))


def image_below_floor(image: str | None) -> bool:
    """Return whether ``image`` carries a version tag older than the supported floor."""
    if not image:
        return False
    version = parse_version(split_image(image)[1])
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
            hint="Upgrade the panel (re-run setup, or update the image tags in /opt/remnawave) and retry.",
            category="user",
        )
    if parsed[0] > REMNAWAVE_TESTED_MAJOR:
        return (
            f"Remnawave {version} is newer than the tested {REMNAWAVE_TESTED_MAJOR}.x line; "
            "continuing, but report any API errors."
        )
    return ""
