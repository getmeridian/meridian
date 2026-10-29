"""The web owner's password hash and optional TOTP second factor, stdlib only.

Passwords are hashed with scrypt (N=2^15, r=8, p=3: OWASP's 32 MiB equivalent of
its N=2^17 baseline, so a 256 MB service survives a burst) under a random salt;
the parameters ride the stored string, so they can rise without a migration.
One hash runs at a time: a flood of guesses queues instead of exhausting memory,
and the login limiter refuses most of them before they reach here.

TOTP follows RFC 6238 (HMAC-SHA1, 6 digits, 30 s steps) with a one-step window on
either side; the caller records the step a code matched so it is never accepted
again (`totp_match`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import threading
import unicodedata
from urllib.parse import quote, urlencode

PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 256
# (log2 N, r, p) for new hashes; existing hashes keep the parameters they were made with.
SCRYPT_PARAMS = (15, 8, 3)
_SCRYPT_MAXMEM = 64 * 1024 * 1024
_HASH_BYTES = 32
_HASHING = threading.Semaphore(1)

TOTP_STEP_SECONDS = 30
TOTP_DIGITS = 6
TOTP_WINDOW = 1
_TOTP_SECRET_BYTES = 20


class PasswordPolicyError(ValueError):
    """A new password the policy refuses; the message is shown to the owner."""


def normalized_password(password: str) -> bytes:
    """The bytes a password hashes as: NFC, so one keyboard's é matches another's."""
    return unicodedata.normalize("NFC", password).encode("utf-8")


def check_password_policy(password: str) -> None:
    length = len(unicodedata.normalize("NFC", password))
    if length < PASSWORD_MIN_LENGTH:
        raise PasswordPolicyError(f"Use at least {PASSWORD_MIN_LENGTH} characters.")
    if length > PASSWORD_MAX_LENGTH:
        raise PasswordPolicyError(f"Use at most {PASSWORD_MAX_LENGTH} characters.")


def hash_password(password: str) -> str:
    """`scrypt$<log2 N>$<r>$<p>$<salt>$<hash>` with URL-safe Base64 fields."""
    check_password_policy(password)
    salt = secrets.token_bytes(16)
    log_n, r, p = SCRYPT_PARAMS
    digest = _scrypt(normalized_password(password), salt, log_n, r, p)
    return "$".join(("scrypt", str(log_n), str(r), str(p), _b64(salt), _b64(digest)))


def verify_password(password: str, stored: str) -> bool:
    """Whether `password` matches `stored`, compared in constant time; a malformed hash never matches."""
    try:
        scheme, log_n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = _scrypt(normalized_password(password), _unb64(salt), int(log_n), int(r), int(p))
        return hmac.compare_digest(digest, _unb64(expected))
    except (ValueError, TypeError):
        return False


def _scrypt(password: bytes, salt: bytes, log_n: int, r: int, p: int) -> bytes:
    if not (10 <= log_n <= 20 and 1 <= r <= 16 and 1 <= p <= 16):
        raise ValueError("scrypt parameters out of range")
    with _HASHING:
        return hashlib.scrypt(password, salt=salt, n=2**log_n, r=r, p=p, maxmem=_SCRYPT_MAXMEM, dklen=_HASH_BYTES)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# -- TOTP (RFC 6238) ---------------------------------------------------------------------


def new_totp_secret() -> str:
    """A random 160-bit secret as unpadded Base32, the form authenticator apps take."""
    return base64.b32encode(secrets.token_bytes(_TOTP_SECRET_BYTES)).decode("ascii").rstrip("=")


def totp_step(now: float) -> int:
    return int(now // TOTP_STEP_SECONDS)


def totp_code(secret: str, step: int) -> str:
    """The code for one 30 s step (RFC 4226 dynamic truncation over HMAC-SHA1)."""
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**TOTP_DIGITS).zfill(TOTP_DIGITS)


def totp_match(secret: str, code: str, *, now: float, last_step: int | None) -> int | None:
    """The step `code` matches within ±1 step and after `last_step`, or None.

    Every candidate is compared, so timing does not reveal which step matched; a
    step at or before the last accepted one is refused, so a code (or an older one
    seen over a shoulder) is never accepted twice.
    """
    code = "".join(code.split())
    if len(code) != TOTP_DIGITS or not code.isascii() or not code.isdigit():
        return None
    current = totp_step(now)
    matched: int | None = None
    for step in range(current - TOTP_WINDOW, current + TOTP_WINDOW + 1):
        if hmac.compare_digest(totp_code(secret, step), code) and (last_step is None or step > last_step):
            matched = step
    return matched


def otpauth_uri(secret: str, *, account: str, issuer: str) -> str:
    """The `otpauth://` URI an authenticator app scans."""
    label = quote(f"{issuer}:{account}", safe="")
    query = urlencode(
        {"secret": secret, "issuer": issuer, "algorithm": "SHA1", "digits": TOTP_DIGITS, "period": TOTP_STEP_SECONDS}
    )
    return f"otpauth://totp/{label}?{query}"
