"""Reality x25519 keypair generation through the control panel.

Remnawave's tools controller generates the pair, so no exit server needs Docker,
Xray, or unzip before its Profile exists. The panel already stores every Reality
private key inside its Profiles, so this exposes nothing new.
"""

from __future__ import annotations

import base64
import binascii
from typing import Protocol

from meridian.core.errors import MeridianError


class X25519Source(Protocol):
    def generate_x25519_keypairs(self) -> list[tuple[str, str]]: ...


def generate_reality_keypair(panel: X25519Source) -> tuple[str, str]:
    """Return one validated ``(private_key, public_key)`` pair."""
    for private_key, public_key in panel.generate_x25519_keypairs():
        if _is_x25519_key(private_key) and _is_x25519_key(public_key) and private_key != public_key:
            return private_key, public_key
    raise MeridianError(
        "Could not generate a Reality x25519 keypair",
        hint="The panel returned no usable keypair; check that it is healthy and run the same command again.",
        category="system",
    )


def _is_x25519_key(value: str) -> bool:
    """Xray encodes x25519 keys as unpadded base64url of 32 bytes."""
    if len(value) != 43:
        return False
    try:
        return len(base64.urlsafe_b64decode(value + "=")) == 32
    except (binascii.Error, ValueError):
        return False
