"""Encrypted snapshots of the control service's state (`meridian.snapshot/v1`).

A snapshot is one file, built by the service and kept by every device that manages
the fleet, so a seized or dead control server never takes the fleet with it:

    meridian-snapshot/v1\\n
    <header: canonical JSON on one line>\\n
    <an age v1 file (age-encryption.org/v1), binary>

The age file is the standard X25519 construction (pyrage, the Rust `rage`
implementation): a random file key wrapped per recipient with X25519 and
HKDF-SHA256, the payload under ChaCha20-Poly1305 in 64 KiB STREAM chunks. Each
device's key and the fleet's recovery key are recipients; a recovery key is an
ordinary age identity, so `age -d -i` opens a snapshot without Meridian.

age has no associated data, so the payload is a ZIP whose `header.json` holds the
header's exact bytes: after decryption they must equal the plaintext header, which
binds fleet ID, epoch, and revision to the authenticated payload. The plaintext
header only says what a file is before anyone decrypts it, and is never trusted
on its own.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass
from typing import Final, Literal

import pyrage
from pydantic import Field, ValidationError
from pyrage import x25519

from meridian.core.agent_types import FleetEpoch, FleetId, Timestamp
from meridian.core.errors import MeridianError
from meridian.core.models import CoreModel

SNAPSHOT_FORMAT: Final = "meridian.snapshot/v1"
MAGIC = b"meridian-snapshot/v1\n"
HEADER_MEMBER = "header.json"
DATABASE_MEMBER = "control.db"
# A snapshot is a SQLite file of users, specs, and revisions: far below this.
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
MAX_HEADER_BYTES = 64 * 1024
_MEMBER = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_PUBLIC_KEY = r"^age1[02-9ac-hj-np-z]{58}$"


class SnapshotError(MeridianError):
    """A snapshot that cannot be read: malformed, tampered with, or not for any key at hand."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message, hint=hint, category="user")


class SnapshotRecipient(CoreModel):
    """One key a snapshot is encrypted to: a device's (by its registration ID) or the recovery key."""

    kind: Literal["device", "recovery"]
    id: str = Field(default="", max_length=64, description="The device registration's ID; empty for recovery.")
    public_key: str = Field(pattern=_PUBLIC_KEY)


class SnapshotHeader(CoreModel):
    format: Literal["meridian.snapshot/v1"] = SNAPSHOT_FORMAT
    fleet_id: FleetId
    fleet_epoch: FleetEpoch
    revision: int = Field(ge=0, description="The newest topology revision the service had recorded; 0 for none.")
    created_at: Timestamp
    service_version: str = Field(max_length=64)
    recipients: list[SnapshotRecipient] = Field(min_length=1, max_length=64)

    def canonical(self) -> bytes:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class KeyPair:
    """An X25519 key pair in age's text forms: `AGE-SECRET-KEY-1...` and `age1...`."""

    secret: str
    public: str


@dataclass(frozen=True)
class Snapshot:
    header: SnapshotHeader
    files: dict[str, bytes]

    @property
    def database(self) -> bytes:
        return self.files[DATABASE_MEMBER]


def generate_key_pair() -> KeyPair:
    identity = x25519.Identity.generate()
    return KeyPair(secret=str(identity), public=str(identity.to_public()))


def normalize_secret_key(text: str) -> str:
    """A secret key as typed or pasted: any case, spaces and line breaks ignored."""
    return "".join(text.split()).upper()


def public_key_of(secret: str) -> str:
    """The public key of a secret key, or `SnapshotError` for anything that is not one."""
    try:
        return str(x25519.Identity.from_str(normalize_secret_key(secret)).to_public())
    except pyrage.IdentityError as exc:
        raise SnapshotError(
            "That is not a valid recovery or device key.",
            hint="A key starts with AGE-SECRET-KEY-1 and has 74 characters; check for a typo.",
        ) from exc


def validate_public_key(text: str) -> str:
    try:
        return str(x25519.Recipient.from_str(text.strip()))
    except pyrage.RecipientError as exc:
        raise SnapshotError(f"Not an X25519 public key: {text[:20]!r}") from exc


def encrypt_snapshot(header: SnapshotHeader, files: dict[str, bytes]) -> bytes:
    """Seal `files` to every recipient the header names."""
    canonical = header.canonical()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(HEADER_MEMBER, canonical)
        for name, data in sorted(files.items()):
            if not _MEMBER.fullmatch(name) or name == HEADER_MEMBER:
                raise ValueError(f"invalid snapshot member name: {name!r}")
            archive.writestr(name, data)
    recipients = [x25519.Recipient.from_str(item.public_key) for item in header.recipients]
    return MAGIC + canonical + b"\n" + bytes(pyrage.encrypt(buffer.getvalue(), recipients))


def read_header(blob: bytes) -> SnapshotHeader:
    """The plaintext header, unauthenticated: for listing and choosing keys, never for trust."""
    header, _sealed = _split(blob)
    return header


def decrypt_snapshot(blob: bytes, secret_keys: list[str]) -> Snapshot:
    """Open a snapshot with any of `secret_keys`; refuse a wrong key, tampering, or a header that does not match."""
    header, sealed = _split(blob)
    identities = []
    for secret in secret_keys:
        try:
            identities.append(x25519.Identity.from_str(normalize_secret_key(secret)))
        except pyrage.IdentityError as exc:
            raise SnapshotError("That is not a valid recovery or device key.") from exc
    try:
        plaintext = bytes(pyrage.decrypt(sealed, identities))
    except pyrage.DecryptError as exc:
        raise SnapshotError(
            "This key cannot open the snapshot, or the file was altered.",
            hint="Use this fleet's recovery key (--recovery-key) or a device key the snapshot was made for.",
        ) from exc
    files = _unzip(plaintext)
    if files.pop(HEADER_MEMBER, None) != header.canonical():
        raise SnapshotError("The snapshot's header does not match its sealed copy; the file was altered.")
    if DATABASE_MEMBER not in files:
        raise SnapshotError("The snapshot holds no control-service database.")
    return Snapshot(header=header, files=files)


def _split(blob: bytes) -> tuple[SnapshotHeader, bytes]:
    if len(blob) > MAX_SNAPSHOT_BYTES:
        raise SnapshotError("The file is too large to be a Meridian snapshot.")
    if not blob.startswith(MAGIC):
        raise SnapshotError("This is not a Meridian snapshot.")
    rest = blob[len(MAGIC) :]
    end = rest.find(b"\n", 0, MAX_HEADER_BYTES)
    if end < 0:
        raise SnapshotError("The snapshot's header is malformed.")
    raw = rest[:end]
    try:
        header = SnapshotHeader.model_validate_json(raw)
    except ValidationError as exc:
        raise SnapshotError("The snapshot's header is malformed or from a newer Meridian.") from exc
    if raw != header.canonical():
        raise SnapshotError("The snapshot's header is not in canonical form; the file was altered.")
    return header, rest[end + 1 :]


def _unzip(plaintext: bytes) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(plaintext)) as archive:
            infos = archive.infolist()
            if len(infos) > 16 or sum(info.file_size for info in infos) > MAX_SNAPSHOT_BYTES:
                raise SnapshotError("The snapshot's payload is too large.")
            files: dict[str, bytes] = {}
            for info in infos:
                if not _MEMBER.fullmatch(info.filename) or info.filename in files:
                    raise SnapshotError("The snapshot's payload names an unexpected file.")
                files[info.filename] = archive.read(info)
            return files
    except (zipfile.BadZipFile, OSError) as exc:
        raise SnapshotError("The snapshot's payload is malformed.") from exc
