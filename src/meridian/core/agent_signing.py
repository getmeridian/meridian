"""Ed25519 signatures of `meridian.agent/v1` desired state, commands, and node proofs.

The signed bytes are the context line, a newline, and the canonical JSON of
the object with its `signature` member removed (see `agent_types`). The service
signs desired state and commands with the key its signing certificate certifies;
`key_id` is the SHA-256 of that certificate's DER, which chains to the fleet's
pinned internal CA. A node signs its `hello` with the key of its node
certificate; that signature sits in `proof.signature` and covers the whole
body with only that member removed.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from meridian.core.agent_types import canonical_json

SignatureContext = Literal[
    "meridian.agent/v1 desired-state",
    "meridian.agent/v1 command",
    "meridian.agent/v1 hello",
]
DESIRED_STATE_CONTEXT: SignatureContext = "meridian.agent/v1 desired-state"
COMMAND_CONTEXT: SignatureContext = "meridian.agent/v1 command"
HELLO_CONTEXT: SignatureContext = "meridian.agent/v1 hello"


def generate_signing_key() -> bytes:
    """Return a fresh raw 32-byte Ed25519 private key."""
    return Ed25519PrivateKey.generate().private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())


def public_key(private_key: bytes) -> bytes:
    """Return the raw 32-byte public key of a raw private key."""
    return Ed25519PrivateKey.from_private_bytes(private_key).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def certificate_key_id(certificate_der: bytes) -> str:
    """Return the `key_id` of a signing certificate: the SHA-256 of its DER in lowercase hex."""
    return hashlib.sha256(certificate_der).hexdigest()


def signed_bytes(document: dict[str, Any], context: SignatureContext) -> bytes:
    """Return exactly what a signature covers: context, newline, canonical JSON without `signature`."""
    unsigned = {name: value for name, value in document.items() if name != "signature"}
    return context.encode("ascii") + b"\n" + canonical_json(unsigned)


def sign(document: dict[str, Any], context: SignatureContext, private_key: bytes, key_id: str) -> dict[str, str]:
    """Return the `signature` member (`ServiceSignature` as JSON) for a document."""
    signer = Ed25519PrivateKey.from_private_bytes(private_key)
    signature = signer.sign(signed_bytes(document, context))
    return {"key_id": key_id, "signature": base64.b64encode(signature).decode("ascii")}


def verify(document: dict[str, Any], context: SignatureContext, public: bytes, key_id: str) -> bool:
    """Whether the document's `signature` member names `key_id` and is valid for the raw public key."""
    member = document.get("signature")
    if not isinstance(member, dict) or member.get("key_id") != key_id:
        return False
    return _valid(public, str(member.get("signature", "")), signed_bytes(document, context))


def proof_signed_bytes(hello: dict[str, Any]) -> bytes:
    """What a `NodeProof` signature covers: the hello body with only `proof.signature` removed."""
    proof = hello.get("proof")
    unsigned_proof = (
        {name: value for name, value in proof.items() if name != "signature"} if isinstance(proof, dict) else proof
    )
    return signed_bytes({**hello, "proof": unsigned_proof}, HELLO_CONTEXT)


def sign_proof(hello: dict[str, Any], private_key: bytes) -> str:
    """Return the base64 `proof.signature` of a hello body."""
    signature = Ed25519PrivateKey.from_private_bytes(private_key).sign(proof_signed_bytes(hello))
    return base64.b64encode(signature).decode("ascii")


def verify_proof(hello: dict[str, Any], public: bytes) -> bool:
    """Whether `proof.signature` of a hello body (as received) is valid for the node's raw public key."""
    proof = hello.get("proof")
    if not isinstance(proof, dict):
        return False
    return _valid(public, str(proof.get("signature", "")), proof_signed_bytes(hello))


def _valid(public: bytes, encoded: str, message: bytes) -> bool:
    try:
        signature = base64.b64decode(encoded, validate=True)
        Ed25519PublicKey.from_public_bytes(public).verify(signature, message)
    except (InvalidSignature, ValueError):
        return False
    return True
