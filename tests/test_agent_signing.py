"""Ed25519 signing of agent documents: what is covered, and what breaks a signature."""

from __future__ import annotations

import base64

import pytest

from meridian.core.agent_signing import (
    COMMAND_CONTEXT,
    DESIRED_STATE_CONTEXT,
    generate_signing_key,
    key_id,
    public_key,
    sign,
    signed_bytes,
    verify,
)


def _signed(document: dict) -> tuple[dict, bytes]:
    private = generate_signing_key()
    return {**document, "signature": sign(document, DESIRED_STATE_CONTEXT, private)}, public_key(private)


def test_signature_covers_context_line_and_canonical_json_without_signature() -> None:
    document = {"b": 1, "a": "é", "signature": {"key_id": "x", "signature": "y"}}
    assert signed_bytes(document, DESIRED_STATE_CONTEXT) == b'meridian.agent/v1 desired-state\n{"a":"\xc3\xa9","b":1}'


def test_key_id_is_sha256_of_the_raw_public_key() -> None:
    document, public = _signed({"revision": 1})
    assert len(public) == 32
    assert document["signature"]["key_id"] == key_id(public)
    assert len(base64.b64decode(document["signature"]["signature"])) == 64


def test_a_valid_signature_verifies() -> None:
    document, public = _signed({"revision": 1, "resources": []})
    assert verify(document, DESIRED_STATE_CONTEXT, public)


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
    assert not verify(tampered, DESIRED_STATE_CONTEXT, key)


def test_a_desired_state_signature_is_not_a_command_signature() -> None:
    document, public = _signed({"revision": 1})
    assert not verify(document, COMMAND_CONTEXT, public)
