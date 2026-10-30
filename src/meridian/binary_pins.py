"""Pinned server binaries and the newer releases `fleet upgrade` may record.

`config.py` holds this CLI's tested pins. `fleet upgrade` can save a newer
release in the same major into `ClusterConfig.binary_pins` (version plus the
sha256 GitHub publishes for each asset), so a fleet takes upstream fixes
without a Meridian release. A saved pin only ever raises the version: an older
or malformed entry yields to the CLI's pin.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from meridian.config import REALM_SHA256, REALM_VERSION

# Runtime-pin names carrying each Realm asset digest into the reviewed plan.
REALM_SHA256_PIN_PREFIX = "realm_sha256."

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]*)?$")

Version = tuple[int, int, int]


def parse_version(value: str) -> Version | None:
    """Parse ``X.Y.Z`` (optionally ``v``-prefixed or with a suffix); None otherwise."""
    match = _VERSION_RE.match(value.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


@dataclass(frozen=True)
class BinaryPin:
    """One release: its X.Y.Z version and the sha256 of each asset, keyed like the CLI's pin."""

    version: str
    sha256: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class BinarySource:
    """Where a pinned binary's releases live; ``asset`` turns a pin key into the release file name."""

    repository: str
    asset: str  # format string with {key}
    tag_prefix: str = "v"
    prereleases: bool = False

    def asset_name(self, key: str) -> str:
        return self.asset.format(key=key)


# Binaries running on servers that `fleet upgrade` may move within their major.
UPGRADABLE_BINARIES: dict[str, BinaryPin] = {
    "realm": BinaryPin(REALM_VERSION, dict(REALM_SHA256)),
}

# Release sources; `scripts/bump_pins.py` also reads the ones `fleet upgrade` leaves to Meridian releases.
BINARY_SOURCES: dict[str, BinarySource] = {
    "realm": BinarySource("zhboner/realm", "realm-{key}.tar.gz"),
    "realitlscanner": BinarySource("XTLS/RealiTLScanner", "{key}"),
    "acme.sh": BinarySource("acmesh-official/acme.sh", "{key}", tag_prefix=""),
}


def valid_binary_pin(name: str, pin: BinaryPin) -> bool:
    """Whether a saved pin names an X.Y.Z release in the CLI pin's major with a digest for every asset."""
    default = UPGRADABLE_BINARIES.get(name)
    if default is None or not _SEMVER_RE.match(pin.version):
        return False
    version, pinned = parse_version(pin.version), parse_version(default.version)
    return (
        version is not None
        and pinned is not None
        and version[0] == pinned[0]
        and set(pin.sha256) == set(default.sha256)
        and all(_SHA256_RE.match(digest) for digest in pin.sha256.values())
    )


def effective_binary_pins(saved: Mapping[str, BinaryPin]) -> dict[str, BinaryPin]:
    """This CLI's binary pins, raised to a newer release `fleet upgrade` saved."""
    pins = dict(UPGRADABLE_BINARIES)
    for name, pin in saved.items():
        if not valid_binary_pin(name, pin):
            continue
        version, default = parse_version(pin.version), parse_version(pins[name].version)
        if version is not None and default is not None and version > default:
            pins[name] = pin
    return pins
