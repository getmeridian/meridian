from __future__ import annotations

import base64

import pytest

from meridian.core.errors import MeridianError
from meridian.reality_keys import generate_reality_keypair


def _key(byte: int) -> str:
    return base64.urlsafe_b64encode(bytes([byte]) * 32).decode().rstrip("=")


class _Panel:
    def __init__(self, keypairs: list[tuple[str, str]]) -> None:
        self.keypairs = keypairs

    def generate_x25519_keypairs(self) -> list[tuple[str, str]]:
        return self.keypairs


def test_keypair_comes_from_the_panel_not_a_server_tool() -> None:
    assert generate_reality_keypair(_Panel([(_key(1), _key(2))])) == (_key(1), _key(2))


def test_malformed_pairs_are_skipped_for_the_first_valid_one() -> None:
    pairs = [("", _key(2)), (_key(1), "short"), (_key(3), _key(3)), (_key(4), _key(5))]
    assert generate_reality_keypair(_Panel(pairs)) == (_key(4), _key(5))


def test_no_usable_pair_is_a_system_error() -> None:
    with pytest.raises(MeridianError) as raised:
        generate_reality_keypair(_Panel([(_key(1) + "==", _key(2))]))
    assert raised.value.category == "system"
