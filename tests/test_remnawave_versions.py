"""Remnawave version policy: floor, tested major, and never-downgrade image tags."""

from __future__ import annotations

import pytest

from meridian.remnawave import RemnawaveError
from meridian.remnawave.versions import (
    ImageRef,
    check_panel_version,
    data_major,
    effective_image,
    find_image,
    image_below_floor,
    image_status,
    parse_image,
    parse_version,
    resolve_data_image,
    resolve_image,
)

_PIN = "remnawave/node:3.4.2"
_DIGEST = "sha256:" + "a" * 64
_OTHER_DIGEST = "sha256:" + "b" * 64
_DIGEST_PIN = f"remnawave/node:3.4.2@{_DIGEST}"


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
        ("remnawave/node:3.4.2", ("remnawave/node", "3.4.2", "")),
        ("docker.io/remnawave/node:3.4.2", ("remnawave/node", "3.4.2", "")),
        ("registry.example:5000/remnawave/node", ("registry.example:5000/remnawave/node", "", "")),
        ("remnawave/node@sha256:abc", ("remnawave/node", "", "sha256:abc")),
        ("remnawave/node:3.4.2@sha256:abc", ("remnawave/node", "3.4.2", "sha256:abc")),
        ("docker.io/library/postgres:17.6-alpine@sha256:abc", ("postgres", "17.6-alpine", "sha256:abc")),
        ("index.docker.io/library/postgres", ("postgres", "", "")),
    ],
)
def test_parse_image_splits_tag_from_digest(image: str, expected: tuple[str, str, str]) -> None:
    assert parse_image(image) == ImageRef(*expected)


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


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        (None, _DIGEST_PIN),  # fresh server pulls the tested content
        ("remnawave/node:3.4.1", _DIGEST_PIN),  # older tag-only: upgrade to the pin
        (f"remnawave/node:3.4.1@{_OTHER_DIGEST}", _DIGEST_PIN),  # older with digest: upgrade
        ("remnawave/node:3.4.2", _DIGEST_PIN),  # same version, no digest: adopt the tested digest
        (f"remnawave/node:3.4.2@{_OTHER_DIGEST}", _DIGEST_PIN),  # same version, re-pushed tag
        (_DIGEST_PIN, _DIGEST_PIN),
        ("remnawave/node:3.6.0", "remnawave/node:3.6.0"),  # newer tag-only operator upgrade: keep
        (f"remnawave/node:4.0.0@{_OTHER_DIGEST}", f"remnawave/node:4.0.0@{_OTHER_DIGEST}"),
        ("remnawave/node:latest", "remnawave/node:latest"),  # floating tag: user-managed
        (f"remnawave/node@{_OTHER_DIGEST}", f"remnawave/node@{_OTHER_DIGEST}"),  # bare digest: user-managed
    ],
)
def test_resolve_image_compares_versions_by_tag_when_the_pin_carries_a_digest(
    existing: str | None, expected: str
) -> None:
    assert resolve_image(_DIGEST_PIN, existing) == expected


@pytest.mark.parametrize(
    "existing",
    [
        f"docker.io/remnawave/node:3.4.2@{_DIGEST}",
        f"remnawave/node@{_DIGEST}",
        f"index.docker.io/remnawave/node:3.4@{_DIGEST}",
    ],
)
def test_resolve_image_keeps_any_spelling_of_the_pinned_content_verbatim(existing: str) -> None:
    # Returning the server's own spelling keeps compose observations converged.
    assert resolve_image(_DIGEST_PIN, existing) == existing


def test_resolve_image_is_idempotent_after_adopting_the_pin() -> None:
    adopted = resolve_image(_DIGEST_PIN, "remnawave/node:3.4.2")
    assert resolve_image(_DIGEST_PIN, adopted) == adopted == _DIGEST_PIN


def test_image_below_floor_reads_the_tag_of_a_digest_reference() -> None:
    assert image_below_floor(f"remnawave/backend:2.8.0@{_DIGEST}")
    assert not image_below_floor(f"remnawave/backend:3.4.5@{_DIGEST}")
    assert not image_below_floor(f"remnawave/backend@{_DIGEST}")


def test_effective_image_finds_digest_references_in_compose() -> None:
    compose = f"services:\n  node:\n    image: 'docker.io/remnawave/node:3.4.2@{_DIGEST}'\n"
    assert find_image(compose, "remnawave/node") == f"docker.io/remnawave/node:3.4.2@{_DIGEST}"
    assert effective_image(_DIGEST_PIN, compose) == f"docker.io/remnawave/node:3.4.2@{_DIGEST}"


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


_PG_PIN = "postgres:17.11-alpine@sha256:" + "b" * 64
_PG_PIN_SPELLED_OUT = "docker.io/library/" + _PG_PIN


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        (None, _PG_PIN),
        ("postgres:17-alpine", _PG_PIN),
        ("postgres:17.10-alpine", _PG_PIN),
        ("postgres:18-alpine", "postgres:18-alpine"),
        ("postgres:17.12-alpine", "postgres:17.12-alpine"),
        ("postgres:latest", "postgres:latest"),
        (
            "docker.io/library/postgres:17.11-alpine@sha256:" + "b" * 64,
            "docker.io/library/postgres:17.11-alpine@sha256:" + "b" * 64,
        ),
    ],
)
def test_resolve_data_image_never_moves_a_database_to_an_older_version(existing: str | None, expected: str) -> None:
    assert resolve_data_image(_PG_PIN, existing) == expected


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        ("postgres:16.9-alpine", "postgres:16.9-alpine"),
        ("postgres:17.10-alpine", _PG_PIN),
        ("postgres:18-alpine", "postgres:18-alpine"),
    ],
)
def test_postgres_never_moves_to_a_newer_major_in_place(existing: str, expected: str) -> None:
    assert resolve_data_image(_PG_PIN, existing, cross_major=False) == expected
    # Valkey reads older data, so its major still moves.
    assert resolve_data_image(_PG_PIN, "postgres:16.9-alpine") == _PG_PIN


@pytest.mark.parametrize(
    ("running", "data", "expected"),
    [
        (None, False, "absent"),
        (_DIGEST_PIN, False, "current"),
        (f"docker.io/remnawave/node@{_DIGEST}", False, "current"),
        ("remnawave/node:3.4.1", False, "upgrade"),
        ("remnawave/node:3.4.2", False, "upgrade"),
        ("remnawave/node:3.5.0", False, "kept_newer"),
        ("remnawave/node:latest", False, "kept_unmanaged"),
        ("ghcr.io/remnawave/node:3.4.2", False, "upgrade"),
        (_PG_PIN, True, "current"),
        ("postgres:17.10-alpine", True, "upgrade"),
        ("postgres:18-alpine", True, "kept_newer"),
        ("postgres:latest", True, "kept_unmanaged"),
        ("postgres:16.9-alpine", True, "held_major"),
    ],
)
def test_image_status_follows_the_setup_policy(running: str | None, data: bool, expected: str) -> None:
    pinned = _PG_PIN if data else _DIGEST_PIN
    assert image_status(pinned, running, data=data, cross_major=not data) == expected


def test_data_major_reads_the_leading_tag_number() -> None:
    assert data_major(_PG_PIN) == 17
    assert data_major("postgres:latest") is None
