"""Remnawave version policy: floor, tested major, and never-downgrade image tags."""

from __future__ import annotations

import pytest

from meridian.remnawave import RemnawaveError
from meridian.remnawave.versions import (
    check_panel_version,
    effective_image,
    find_image,
    image_below_floor,
    parse_version,
    resolve_image,
    split_image,
)

_PIN = "remnawave/node:3.4.2"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("3.4.5", (3, 4, 5)),
        ("v3.10.0", (3, 10, 0)),
        ("3.5.0-beta.1", (3, 5, 0)),
        ("3", None),
        ("latest", None),
        ("dev", None),
    ],
)
def test_parse_version(value: str, expected: tuple[int, int, int] | None) -> None:
    assert parse_version(value) == expected


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        ("remnawave/node:3.4.2", ("remnawave/node", "3.4.2")),
        ("docker.io/remnawave/node:3.4.2", ("remnawave/node", "3.4.2")),
        ("registry.example:5000/remnawave/node", ("registry.example:5000/remnawave/node", "")),
        ("remnawave/node@sha256:abc", ("remnawave/node", "@sha256:abc")),
    ],
)
def test_split_image(image: str, expected: tuple[str, str]) -> None:
    assert split_image(image) == expected


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        (None, _PIN),  # fresh server
        ("remnawave/node:2.8.0", _PIN),  # older than the pin: upgrade
        ("remnawave/node:3.4.1", _PIN),  # patch behind the pin: upgrade, never a downgrade
        ("remnawave/node:3.4.2", "remnawave/node:3.4.2"),
        ("remnawave/node:3.6.0", "remnawave/node:3.6.0"),  # operator upgrade: keep
        ("docker.io/remnawave/node:4.0.0", "docker.io/remnawave/node:4.0.0"),
        ("remnawave/node:3", "remnawave/node:3"),  # floating tag: user-managed
        ("remnawave/node:latest", "remnawave/node:latest"),
        ("remnawave/node", "remnawave/node"),
        ("remnawave/node@sha256:abc", "remnawave/node@sha256:abc"),
        ("ghcr.io/fork/node:9.9.9", _PIN),  # different repository is not ours to keep
    ],
)
def test_resolve_image_never_downgrades(existing: str | None, expected: str) -> None:
    assert resolve_image(_PIN, existing) == expected


def test_effective_image_reads_the_matching_compose_service() -> None:
    compose = (
        "services:\n"
        "  db:\n    image: postgres:17-alpine\n"
        '  remnawave:\n    image: "remnawave/backend:3.5.0"  # bumped by operator\n'
        "  sub:\n    image: remnawave/subscription-page:7.2.6\n"
    )

    assert find_image(compose, "remnawave/backend") == "remnawave/backend:3.5.0"
    assert effective_image("remnawave/backend:3.4.5", compose) == "remnawave/backend:3.5.0"
    assert effective_image("remnawave/subscription-page:8.0.0", compose) == "remnawave/subscription-page:8.0.0"
    assert effective_image("remnawave/node:3.4.2", compose) == "remnawave/node:3.4.2"
    assert effective_image("remnawave/node:3.4.2", None) == "remnawave/node:3.4.2"


def test_image_below_floor() -> None:
    assert image_below_floor("remnawave/backend:2.8.0")
    assert not image_below_floor("remnawave/backend:3.4.0")
    assert not image_below_floor("remnawave/backend:latest")
    assert not image_below_floor(None)


def test_check_panel_version_accepts_tested_line() -> None:
    assert check_panel_version("3.4.5") == ""
    assert check_panel_version("3.9.0") == ""


def test_check_panel_version_warns_on_newer_major_and_unknown_builds() -> None:
    assert "newer than the tested 3.x" in check_panel_version("4.0.0")
    assert "unrecognized version" in check_panel_version("development")


@pytest.mark.parametrize("version", ["2.8.0", "3.3.9"])
def test_check_panel_version_refuses_below_floor(version: str) -> None:
    with pytest.raises(RemnawaveError, match="minimum supported 3.4.0") as exc_info:
        check_panel_version(version)
    assert exc_info.value.category == "user"
