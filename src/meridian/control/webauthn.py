"""Verify WebAuthn owner proofs with the existing cryptography dependency.

Registration requests none attestation; no attestation trust or device identity
is retained. COSE ES256/P-256 and EdDSA/Ed25519 keys become minimal DER public
keys. Assertions verify the exact authenticator bytes and client-data hash.
"""

import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from meridian.control.errors import ServiceError
from meridian.control.passkey_models import AssertionCredential, RegistrationCredential
from meridian.control.webauthn_cbor import CborError, Decoder, decode
from meridian.core.inputs import validate_hostname_value

MAX_CREDENTIAL_BYTES = 1024


@dataclass(frozen=True)
class RelyingParty:
    id: str
    origin: str


@dataclass(frozen=True)
class RegisteredKey:
    credential_id: str
    public_key: bytes
    algorithm: Literal[-7, -8]
    sign_count: int


def relying_party(public_url: str) -> RelyingParty | None:
    """Only the reviewed HTTPS domain establishes an RP; a request's Host never does."""
    try:
        parsed = urlsplit(public_url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None:
            return None
        try:
            ipaddress.ip_address(host)
            return None
        except ValueError:
            pass
        canonical = validate_hostname_value(host)
        if canonical != host.lower():
            return None
        port = parsed.port
        origin = f"https://{canonical}" + (f":{port}" if port is not None and port != 443 else "")
        return RelyingParty(canonical, origin)
    except ValueError:
        return None


def encode_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def decode_bytes(value: str, *, maximum: int) -> bytes:
    """Refuse noncanonical base64url, padding, and oversized values before binary parsing."""
    try:
        if len(value) > (maximum * 4 + 2) // 3:
            raise invalid_passkey()
        result = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise invalid_passkey() from exc
    if not result or len(result) > maximum or encode_bytes(result) != value:
        raise invalid_passkey()
    return result


def invalid_passkey() -> ServiceError:
    return ServiceError(
        "passkey_invalid", "This passkey could not be verified. Try another passkey or your password.", 401
    )


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Repeated client-data field.")
        result[key] = value
    return result


def _client_data(value: str, challenge: bytes, rp: RelyingParty, kind: str) -> bytes:
    raw = decode_bytes(value, maximum=8192)
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_fields)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise invalid_passkey() from exc
    if not isinstance(data, dict) or data.get("type") != kind or not isinstance(data.get("challenge"), str):
        raise invalid_passkey()
    if data.get("origin") != rp.origin or data.get("crossOrigin", False) is not False:
        raise ServiceError("passkey_origin", "The passkey's origin does not match this console.", 401)
    if "topOrigin" in data and data["topOrigin"] != rp.origin:
        raise ServiceError("passkey_origin", "The passkey's origin does not match this console.", 401)
    if not hmac.compare_digest(decode_bytes(data["challenge"], maximum=32), challenge):
        raise invalid_passkey()
    return raw


def _authenticator(raw: bytes, rp: RelyingParty, *, registration: bool) -> tuple[int, int]:
    if len(raw) < 37:
        raise invalid_passkey()
    if not hmac.compare_digest(raw[:32], hashlib.sha256(rp.id.encode("utf-8")).digest()):
        raise ServiceError("passkey_rp", "The passkey does not belong to this console's domain.", 401)
    flags = raw[32]
    if flags & 0x05 != 0x05:
        raise ServiceError("passkey_verification", "Unlock your passkey with its PIN, fingerprint, or face check.", 401)
    if flags & 0x10 and not flags & 0x08:  # backup state is only valid for a backup-eligible credential
        raise invalid_passkey()
    if bool(flags & 0x40) != registration:
        raise invalid_passkey()
    return flags, int.from_bytes(raw[33:37])


def _extensions(decoder: Decoder, flags: int) -> None:
    if flags & 0x80 and not isinstance(decoder.read(), dict):
        raise invalid_passkey()
    if decoder.offset != len(decoder.data):
        raise invalid_passkey()


def _public_key(cose: Any) -> tuple[bytes, Literal[-7, -8]]:
    if not isinstance(cose, dict) or any(type(cose.get(key)) is not int for key in (1, 3, -1)):
        raise invalid_passkey()
    x = cose.get(-2)
    if not isinstance(x, bytes) or len(x) != 32:
        raise invalid_passkey()
    public: ec.EllipticCurvePublicKey | ed25519.Ed25519PublicKey
    if (cose[1], cose[3], cose[-1]) == (2, -7, 1):
        y = cose.get(-3)
        if not isinstance(y, bytes) or len(y) != 32:
            raise invalid_passkey()
        public = ec.EllipticCurvePublicNumbers(int.from_bytes(x), int.from_bytes(y), ec.SECP256R1()).public_key()
        algorithm: Literal[-7, -8] = -7
    elif (cose[1], cose[3], cose[-1]) == (1, -8, 6):
        public = ed25519.Ed25519PublicKey.from_public_bytes(x)
        algorithm = -8
    else:
        raise ServiceError("passkey_algorithm", "Use a passkey that supports ES256 or Ed25519.", 400)
    return public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo), algorithm


def verify_registration(credential: RegistrationCredential, *, challenge: bytes, rp: RelyingParty) -> RegisteredKey:
    _client_data(credential.response.client_data_json, challenge, rp, "webauthn.create")
    credential_id = decode_bytes(credential.raw_id, maximum=MAX_CREDENTIAL_BYTES)
    if credential.id != encode_bytes(credential_id):
        raise invalid_passkey()
    try:
        attestation = decode(decode_bytes(credential.response.attestation_object, maximum=24576))
        if not isinstance(attestation, dict) or set(attestation) != {"fmt", "attStmt", "authData"}:
            raise invalid_passkey()
        if attestation["fmt"] != "none" or attestation["attStmt"] != {}:
            raise ServiceError("passkey_attestation", "Create a passkey without device attestation.", 400)
        raw = attestation["authData"]
        if not isinstance(raw, bytes):
            raise invalid_passkey()
        flags, sign_count = _authenticator(raw, rp, registration=True)
        decoder = Decoder(raw)
        decoder.take(37 + 16)  # the AAGUID is ignored, never looked up or stored
        size = int.from_bytes(decoder.take(2))
        if not 1 <= size <= MAX_CREDENTIAL_BYTES or decoder.take(size) != credential_id:
            raise invalid_passkey()
        public_key, algorithm = _public_key(decoder.read())
        _extensions(decoder, flags)
        return RegisteredKey(credential.id, public_key, algorithm, sign_count)
    except (CborError, ValueError) as exc:
        raise invalid_passkey() from exc


def verify_assertion(
    credential: AssertionCredential,
    *,
    challenge: bytes,
    rp: RelyingParty,
    public_key: bytes,
    algorithm: int,
    sign_count: int,
    user_handle: bytes,
) -> int:
    client = _client_data(credential.response.client_data_json, challenge, rp, "webauthn.get")
    if credential.id != encode_bytes(decode_bytes(credential.raw_id, maximum=MAX_CREDENTIAL_BYTES)):
        raise invalid_passkey()
    handle = credential.response.user_handle
    if handle is not None and not hmac.compare_digest(decode_bytes(handle, maximum=64), user_handle):
        raise invalid_passkey()
    raw = decode_bytes(credential.response.authenticator_data, maximum=3072)
    flags, counter = _authenticator(raw, rp, registration=False)
    try:
        decoder = Decoder(raw)
        decoder.take(37)
        _extensions(decoder, flags)
        signature = decode_bytes(credential.response.signature, maximum=144)
        signed = raw + hashlib.sha256(client).digest()
        public = serialization.load_der_public_key(public_key)
        if algorithm == -7 and isinstance(public, ec.EllipticCurvePublicKey) and isinstance(public.curve, ec.SECP256R1):
            public.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
        elif algorithm == -8 and isinstance(public, ed25519.Ed25519PublicKey):
            public.verify(signature, signed)
        else:
            raise invalid_passkey()
    except (CborError, ValueError, InvalidSignature) as exc:
        raise invalid_passkey() from exc
    if (sign_count or counter) and counter <= sign_count:
        raise ServiceError(
            "passkey_counter", "This passkey's counter did not advance. Use another passkey or your password.", 401
        )
    return counter
