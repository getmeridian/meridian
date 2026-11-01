"""A local WebAuthn authenticator: real ES256/Ed25519 signatures, no browser or device metadata."""

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from meridian.control.passkey_models import AssertionCredential, RegistrationCredential

RP_ID = "console.example.test"
ORIGIN = f"https://{RP_ID}"
PASSWORD = "correct horse battery"


@dataclass
class Clock:
    now: float = 1_800_000_000

    def __call__(self) -> float:
        return self.now


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def cbor(value: Any) -> bytes:
    """Encode test authenticator output independently of the production decoder."""
    if isinstance(value, int):
        major, length, body = (0, value, b"") if value >= 0 else (1, -1 - value, b"")
    elif isinstance(value, bytes):
        major, length, body = 2, len(value), value
    elif isinstance(value, str):
        body = value.encode()
        major, length = 3, len(body)
    elif isinstance(value, dict):
        major, length, body = 5, len(value), b"".join(cbor(k) + cbor(v) for k, v in value.items())
    else:
        raise TypeError(value)
    if length < 24:
        return bytes([major * 32 + length]) + body
    width = 1 if length <= 255 else 2 if length <= 65535 else 4
    return bytes([major * 32 + {1: 24, 2: 25, 4: 26}[width]]) + length.to_bytes(width) + body


class Authenticator:
    def __init__(self, algorithm: int = -7) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1()) if algorithm == -7 else ed25519.Ed25519PrivateKey.generate()
        self.algorithm = algorithm
        self.credential_id = secrets.token_bytes(32)
        self.user_handle: str | None = None

    @property
    def id(self) -> str:
        return b64(self.credential_id)

    def cose(self) -> dict[int, int | bytes]:
        public = self.key.public_key()
        if isinstance(public, ec.EllipticCurvePublicKey):
            numbers = public.public_numbers()
            return {1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32), -3: numbers.y.to_bytes(32)}
        return {1: 1, 3: -8, -1: 6, -2: public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)}

    def registration(
        self, options: dict[str, Any], *, count: int = 5, rp_id: str = RP_ID, flags: int = 0x45, **client: Any
    ) -> RegistrationCredential:
        self.user_handle = options["user"]["id"]
        client_json = json.dumps(
            {"type": "webauthn.create", "challenge": options["challenge"], "origin": ORIGIN, **client}
        ).encode()
        auth_data = (
            hashlib.sha256(rp_id.encode()).digest()
            + bytes([flags])
            + count.to_bytes(4)
            + b"\0" * 16
            + len(self.credential_id).to_bytes(2)
            + self.credential_id
            + cbor(self.cose())
        )
        return RegistrationCredential.model_validate(
            {
                "id": self.id,
                "rawId": self.id,
                "type": "public-key",
                "response": {
                    "clientDataJSON": b64(client_json),
                    "attestationObject": b64(cbor({"fmt": "none", "attStmt": {}, "authData": auth_data})),
                },
            }
        )

    def assertion(
        self, options: dict[str, Any], *, count: int = 6, rp_id: str = RP_ID, flags: int = 0x05, **client: Any
    ) -> AssertionCredential:
        client_json = json.dumps(
            {"type": "webauthn.get", "challenge": options["challenge"], "origin": ORIGIN, **client}
        ).encode()
        auth_data = hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + count.to_bytes(4)
        signed = auth_data + hashlib.sha256(client_json).digest()
        signature = (
            self.key.sign(signed, ec.ECDSA(hashes.SHA256()))
            if isinstance(self.key, ec.EllipticCurvePrivateKey)
            else self.key.sign(signed)
        )
        return AssertionCredential.model_validate(
            {
                "id": self.id,
                "rawId": self.id,
                "type": "public-key",
                "response": {
                    "clientDataJSON": b64(client_json),
                    "authenticatorData": b64(auth_data),
                    "signature": b64(signature),
                    "userHandle": self.user_handle,
                },
            }
        )
