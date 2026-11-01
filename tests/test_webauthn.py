"""Hostile WebAuthn bytes are bounded and refused before a key or session can be granted."""

import json

import pytest

from meridian.control.errors import ServiceError
from meridian.control.webauthn import RelyingParty, decode_bytes, verify_registration
from tests.support.passkeys import ORIGIN, RP_ID, Authenticator, b64, cbor

CHALLENGE = b"x" * 32
OPTIONS = {"challenge": b64(CHALLENGE), "user": {"id": b64(b"y" * 32)}}
RP = RelyingParty(RP_ID, ORIGIN)


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\x9f\x01\xff",
        b"\xc0\xa0",
        b"\xa2\x61a\x01\x61a\x02",
        b"\x99\xff\xff",
        b"\x81" * 10 + b"\x00",
        b"\xa1\x41x\x01",
        b"\x63\xff\xff\xff",
        b"\xa0\x00",
        b"\xfa\x00\x00\x00\x00",
    ],
    ids=[
        "empty",
        "indefinite",
        "tag",
        "duplicate",
        "collection-budget",
        "depth-budget",
        "bytes-key",
        "invalid-utf8",
        "trailing",
        "float",
    ],
)
def test_malformed_cbor_attestations_raise_a_plain_refusal(raw: bytes) -> None:
    credential = Authenticator().registration(OPTIONS)
    response = credential.response.model_copy(update={"attestation_object": b64(raw)})
    with pytest.raises(ServiceError) as refused:
        verify_registration(credential.model_copy(update={"response": response}), challenge=CHALLENGE, rp=RP)
    assert refused.value.code == "passkey_invalid"


@pytest.mark.parametrize("value", ["_x", "AA=", "+w", "AB", "x" * 1400])
def test_binary_fields_need_bounded_canonical_base64url(value: str) -> None:
    with pytest.raises(ServiceError):
        decode_bytes(value, maximum=1024)


def test_registration_refuses_duplicate_signed_client_data_fields() -> None:
    credential = Authenticator().registration(OPTIONS)
    client = json.dumps({"type": "webauthn.create", "challenge": b64(CHALLENGE), "origin": ORIGIN})
    client = client[:-1] + ',"origin":"' + ORIGIN + '"}'
    response = credential.response.model_copy(update={"client_data_json": b64(client.encode())})
    with pytest.raises(ServiceError) as refused:
        verify_registration(credential.model_copy(update={"response": response}), challenge=CHALLENGE, rp=RP)
    assert refused.value.code == "passkey_invalid"


@pytest.mark.parametrize("flags", [0x05, 0x55], ids=["missing-attested-data", "backup-state-without-eligibility"])
def test_registration_refuses_invalid_authenticator_flags(flags: int) -> None:
    with pytest.raises(ServiceError) as refused:
        verify_registration(Authenticator().registration(OPTIONS, flags=flags), challenge=CHALLENGE, rp=RP)
    assert refused.value.code == "passkey_invalid"


def test_registration_does_not_accept_device_attestation() -> None:
    credential = Authenticator().registration(OPTIONS)
    attestation = cbor({"fmt": "packed", "attStmt": {"x5c": b"device-certificate"}, "authData": b"ignored"})
    response = credential.response.model_copy(update={"attestation_object": b64(attestation)})
    with pytest.raises(ServiceError) as refused:
        verify_registration(credential.model_copy(update={"response": response}), challenge=CHALLENGE, rp=RP)
    assert refused.value.code == "passkey_attestation"


def test_registration_refuses_a_credential_id_different_from_its_attested_id() -> None:
    credential = Authenticator().registration(OPTIONS)
    other_id = b64(b"z" * 32)
    with pytest.raises(ServiceError) as refused:
        verify_registration(
            credential.model_copy(update={"id": other_id, "raw_id": other_id}), challenge=CHALLENGE, rp=RP
        )
    assert refused.value.code == "passkey_invalid"
