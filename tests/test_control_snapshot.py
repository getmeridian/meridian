"""The encrypted snapshot format: age to N recipients, a header bound to the sealed payload."""

from __future__ import annotations

import pytest

from meridian.control.snapshot import (
    MAGIC,
    SnapshotError,
    SnapshotHeader,
    SnapshotRecipient,
    decrypt_snapshot,
    encrypt_snapshot,
    generate_key_pair,
    normalize_secret_key,
    public_key_of,
    read_header,
)

FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
DEVICE = generate_key_pair()
RECOVERY = generate_key_pair()


def _header(**changes: object) -> SnapshotHeader:
    fields: dict[str, object] = {
        "fleet_id": FLEET_ID,
        "fleet_epoch": 3,
        "revision": 12,
        "created_at": "2026-10-29T12:00:00Z",
        "service_version": "4.0.0",
        "recipients": [
            SnapshotRecipient(kind="device", id="d1", public_key=DEVICE.public),
            SnapshotRecipient(kind="recovery", public_key=RECOVERY.public),
        ],
    }
    fields.update(changes)
    return SnapshotHeader.model_validate(fields)


def _sealed() -> bytes:
    return encrypt_snapshot(_header(), {"control.db": b"SQLite format 3\x00" + b"x" * 5000})


@pytest.mark.parametrize("secret", [DEVICE.secret, RECOVERY.secret], ids=["device", "recovery"])
def test_every_recipient_opens_the_snapshot(secret: str) -> None:
    snapshot = decrypt_snapshot(_sealed(), [secret])

    assert snapshot.header == _header()
    assert snapshot.database.startswith(b"SQLite format 3")


def test_the_payload_is_a_standard_age_file_after_the_header() -> None:
    blob = _sealed()
    _magic, header, sealed = blob.split(b"\n", 2)

    assert blob.startswith(MAGIC)
    assert header == _header().canonical()
    assert sealed.startswith(b"age-encryption.org/v1\n")
    assert b"SQLite" not in blob


def test_a_recovery_key_reads_whatever_way_it_was_typed() -> None:
    spaced = " ".join(RECOVERY.secret.lower()[index : index + 8] for index in range(0, len(RECOVERY.secret), 8))

    assert normalize_secret_key(spaced) == RECOVERY.secret
    assert public_key_of(spaced) == RECOVERY.public
    assert decrypt_snapshot(_sealed(), [spaced]).header.fleet_epoch == 3


def test_a_key_the_snapshot_was_not_made_for_is_refused() -> None:
    with pytest.raises(SnapshotError, match="cannot open"):
        decrypt_snapshot(_sealed(), [generate_key_pair().secret])


def test_a_malformed_key_is_refused_before_decrypting() -> None:
    with pytest.raises(SnapshotError, match="not a valid"):
        decrypt_snapshot(_sealed(), ["AGE-SECRET-KEY-1NOTAKEY"])


@pytest.mark.parametrize(
    ("field", "value"),
    [("fleet_epoch", 4), ("revision", 13), ("fleet_id", "0f1e2d3c-4b5a-4968-8776-655443322111")],
    ids=["epoch", "revision", "fleet"],
)
def test_a_relabelled_header_is_refused_after_decryption(field: str, value: object) -> None:
    blob = _sealed()
    forged = blob.replace(_header().canonical(), _header(**{field: value}).canonical())

    assert read_header(forged).model_dump()[field] == value
    with pytest.raises(SnapshotError, match="does not match its sealed copy"):
        decrypt_snapshot(forged, [DEVICE.secret])


def test_a_header_spliced_onto_another_snapshot_is_refused() -> None:
    other = encrypt_snapshot(_header(revision=99), {"control.db": b"other"})
    forged = MAGIC + _header().canonical() + b"\n" + other.split(b"\n", 2)[2]

    with pytest.raises(SnapshotError, match="does not match its sealed copy"):
        decrypt_snapshot(forged, [DEVICE.secret])


def test_a_header_that_is_not_canonical_is_refused() -> None:
    blob = _sealed()
    loose = blob.replace(_header().canonical(), _header().canonical().replace(b",", b", ", 1))

    with pytest.raises(SnapshotError, match="canonical"):
        read_header(loose)


@pytest.mark.parametrize("offset", [30, 120, -40], ids=["age-header", "stanza-body", "payload"])
def test_flipping_one_byte_of_the_sealed_part_is_refused(offset: int) -> None:
    blob = bytearray(_sealed())
    start = len(MAGIC) + len(_header().canonical()) + 1
    position = start + offset if offset >= 0 else len(blob) + offset
    blob[position] ^= 0x01

    with pytest.raises(SnapshotError):
        decrypt_snapshot(bytes(blob), [DEVICE.secret])


@pytest.mark.parametrize(
    "blob",
    [b"", b"SQLite format 3\x00", MAGIC + b"{not json\n", MAGIC + b'{"format":"meridian.snapshot/v9"}\n'],
    ids=["empty", "raw-db", "bad-json", "newer-format"],
)
def test_something_that_is_not_a_snapshot_is_refused(blob: bytes) -> None:
    with pytest.raises(SnapshotError):
        read_header(blob)
