"""Ed25519 signing of agent documents and node proofs: what is covered, and what breaks a signature."""

from __future__ import annotations

import base64
import hashlib

import pytest

from meridian.core.agent_signing import (
    COMMAND_CONTEXT,
    DESIRED_STATE_CONTEXT,
    certificate_key_id,
    generate_signing_key,
    proof_signed_bytes,
    public_key,
    sign,
    sign_proof,
    signed_bytes,
    verify,
    verify_proof,
)

KEY_ID = certificate_key_id(b"certificate DER")


def _signed(document: dict) -> tuple[dict, bytes]:
    private = generate_signing_key()
    return {**document, "signature": sign(document, DESIRED_STATE_CONTEXT, private, KEY_ID)}, public_key(private)


def test_signature_covers_context_line_and_canonical_json_without_signature() -> None:
    document = {"b": 1, "a": "é", "signature": {"key_id": "x", "signature": "y"}}
    assert signed_bytes(document, DESIRED_STATE_CONTEXT) == b'meridian.agent/v1 desired-state\n{"a":"\xc3\xa9","b":1}'


def test_key_id_is_sha256_of_the_signing_certificate_der() -> None:
    document, public = _signed({"revision": 1})
    assert len(public) == 32
    assert document["signature"]["key_id"] == hashlib.sha256(b"certificate DER").hexdigest()
    assert len(base64.b64decode(document["signature"]["signature"])) == 64


def test_a_valid_signature_verifies() -> None:
    document, public = _signed({"revision": 1, "resources": []})
    assert verify(document, DESIRED_STATE_CONTEXT, public, KEY_ID)


def test_a_signature_under_another_certificate_does_not_verify() -> None:
    document, public = _signed({"revision": 1})
    assert not verify(document, DESIRED_STATE_CONTEXT, public, certificate_key_id(b"other"))


@pytest.mark.parametrize(
    "tamper",
    [
        lambda doc, public: ({**doc, "revision": 2}, public),
        lambda doc, public: (doc, public_key(generate_signing_key())),
        lambda doc, public: ({k: v for k, v in doc.items() if k != "signature"}, public),
        lambda doc, public: ({**doc, "signature": {**doc["signature"], "signature": "!"}}, public),
    ],
    ids=["content-changed", "other-key", "unsigned", "garbage-signature"],
)
def test_a_tampered_document_does_not_verify(tamper) -> None:
    document, public = _signed({"revision": 1})
    tampered, key = tamper(document, public)
    assert not verify(tampered, DESIRED_STATE_CONTEXT, key, KEY_ID)


def test_a_desired_state_signature_is_not_a_command_signature() -> None:
    document, public = _signed({"revision": 1})
    assert not verify(document, COMMAND_CONTEXT, public, KEY_ID)


def test_a_node_proof_covers_the_whole_hello_except_its_own_signature() -> None:
    hello = {"type": "hello", "node_id": "main", "proof": {"nonce": "n", "issued_at": "t", "signature": "x"}}
    assert proof_signed_bytes(hello) == (
        b'meridian.agent/v1 hello\n{"node_id":"main","proof":{"issued_at":"t","nonce":"n"},"type":"hello"}'
    )
    private = generate_signing_key()
    hello["proof"]["signature"] = sign_proof(hello, private)
    assert verify_proof(hello, public_key(private))
    assert not verify_proof({**hello, "node_id": "other"}, public_key(private))
    assert not verify_proof(hello, public_key(generate_signing_key()))
