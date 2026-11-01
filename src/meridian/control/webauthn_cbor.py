"""Bounded CBOR data decoding for WebAuthn's none attestation and COSE public keys.

Only definite lengths, data maps/arrays, integers, bytes, UTF-8, booleans, and null
are needed. Tags, floats, indefinite lengths, duplicate keys, and excessive work
are refused. No attestation certificates, identities, or trust chains are read.
"""

from typing import Any

MAX_CBOR_BYTES = 24576
MAX_ITEMS = 256
MAX_DEPTH = 8
MAX_COLLECTION = 64


class CborError(ValueError):
    """Malformed or unsupported CBOR data."""


class Decoder:
    def __init__(self, data: bytes) -> None:
        if len(data) > MAX_CBOR_BYTES:
            raise CborError("CBOR is too large.")
        self.data = data
        self.offset = 0
        self.items = 0

    def take(self, length: int) -> bytes:
        if length < 0 or length > len(self.data) - self.offset:
            raise CborError("Truncated CBOR.")
        value = self.data[self.offset : self.offset + length]
        self.offset += length
        return value

    def read(self, depth: int = 0) -> Any:
        self.items += 1
        if depth > MAX_DEPTH or self.items > MAX_ITEMS:
            raise CborError("CBOR is too complex.")
        first = self.take(1)[0]
        major, info = first >> 5, first & 31
        if major == 7:
            if info in (20, 21, 22):
                return {20: False, 21: True, 22: None}[info]
            raise CborError("Unsupported CBOR value.")
        if major > 5 or info > 27:
            raise CborError("Unsupported CBOR encoding.")
        length = info if info < 24 else int.from_bytes(self.take(1 << (info - 24)))
        if major == 0:
            return length
        if major == 1:
            return -1 - length
        if major in (2, 3):
            raw = self.take(length)
            if major == 2:
                return raw
            try:
                return raw.decode("utf-8")
            except UnicodeError as exc:
                raise CborError("Invalid CBOR text.") from exc
        if length > MAX_COLLECTION:
            raise CborError("CBOR collection is too large.")
        if major == 4:
            return [self.read(depth + 1) for _ in range(length)]
        result: dict[int | str, Any] = {}
        for _ in range(length):
            key = self.read(depth + 1)
            if type(key) not in (str, int) or key in result:
                raise CborError("Invalid or repeated CBOR key.")
            result[key] = self.read(depth + 1)
        return result


def decode(data: bytes) -> Any:
    decoder = Decoder(data)
    value = decoder.read()
    if decoder.offset != len(data):
        raise CborError("Trailing CBOR data.")
    return value
