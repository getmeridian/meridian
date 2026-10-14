"""Third-party apt keys are trusted only when their fingerprints are pinned."""

from __future__ import annotations

from meridian.provision.apt_keys import primary_fingerprints, verify_apt_key
from tests.support.mock_connection import MockConnection

_DOCKER = (
    "pub:-:4096:1:8D81803C0EBFCD88:1487788586:::-:::scESA::::::23::0:\n"
    "fpr:::::::::9DC858229FC7DD38854AE2D88D81803C0EBFCD88:\n"
    "sub:-:4096:1:7EA0A9C3F273FCD8:1487792064::::::s::::::23:\n"
    "fpr:::::::::D3306A018370199E527AE7997EA0A9C3F273FCD8:\n"
)


def test_only_primary_fingerprints_are_collected() -> None:
    assert primary_fingerprints(_DOCKER) == {"9DC858229FC7DD38854AE2D88D81803C0EBFCD88"}


def test_pinned_key_is_accepted() -> None:
    conn = MockConnection()
    conn.when("gpg --show-keys", stdout=_DOCKER)

    assert verify_apt_key(conn, "/etc/apt/keyrings/docker.asc", "docker") == ""
    conn.assert_not_called_with_pattern("rm -f")


def test_substituted_key_is_rejected_and_deleted() -> None:
    conn = MockConnection()
    conn.when("gpg --show-keys", stdout="pub:-:4096:1:AAAA:::::\nfpr:::::::::" + "A" * 40 + ":\n")

    error = verify_apt_key(conn, "/etc/apt/keyrings/docker.asc", "docker")

    assert "unexpected docker signing key" in error
    conn.assert_called_with_pattern("rm -f /etc/apt/keyrings/docker.asc")


def test_unreadable_key_is_rejected() -> None:
    conn = MockConnection()
    conn.when("gpg --show-keys", rc=2)

    assert "could not read" in verify_apt_key(conn, "/etc/apt/keyrings/nginx.asc", "nginx")
