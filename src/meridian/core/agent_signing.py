"""Ed25519 signatures of `meridian.agent/v1` desired state and commands.

The signed bytes are the context line, a newline, and the canonical JSON of
the object with its `signature` member removed (see `agent_types`). Until the
fleet's internal CA exists, `key_id` is the SHA-256 of the raw 32-byte public
key, which SSH bootstrap pins on every node.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from meridian.core.agent_types import canonical_json

SignatureContext = Literal["meridian.agent/v1 desired-state", "meridian.agent/v1 command"]
DESIRED_STATE_CONTEXT: SignatureContext = "meridian.agent/v1 desired-state"
COMMAND_CONTEXT: SignatureContext = "meridian.agent/v1 command"


def generate_signing_key() -> bytes:
    """Return a fresh raw 32-byte Ed25519 private key."""
    return Ed25519PrivateKey.generate().private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())


def public_key(private_key: bytes) -> bytes:
    """Return the raw 32-byte public key of a raw private key."""
    return Ed25519PrivateKey.from_private_bytes(private_key).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def key_id(public: bytes) -> str:
    """Return the `key_id` of a raw public key: its SHA-256 in lowercase hex."""
    return hashlib.sha256(public).hexdigest()


def signed_bytes(document: dict[str, Any], context: SignatureContext) -> bytes:
    """Return exactly what a signature covers: context, newline, canonical JSON without `signature`."""
    unsigned = {name: value for name, value in document.items() if name != "signature"}
    return context.encode("ascii") + b"\n" + canonical_json(unsigned)


def sign(document: dict[str, Any], context: SignatureContext, private_key: bytes) -> dict[str, str]:
    """Return the `signature` member (`ServiceSignature` as JSON) for a document."""
    signer = Ed25519PrivateKey.from_private_bytes(private_key)
    signature = signer.sign(signed_bytes(document, context))
    return {"key_id": key_id(public_key(private_key)), "signature": base64.b64encode(signature).decode("ascii")}


def verify(document: dict[str, Any], context: SignatureContext, public: bytes) -> bool:
    """Whether the document's `signature` member is valid for the raw public key."""
    member = document.get("signature")
    if not isinstance(member, dict) or member.get("key_id") != key_id(public):
        return False
    try:
        signature = base64.b64decode(str(member.get("signature", "")), validate=True)
        Ed25519PublicKey.from_public_bytes(public).verify(signature, signed_bytes(document, context))
    except (InvalidSignature, ValueError):
        return False
    return True
