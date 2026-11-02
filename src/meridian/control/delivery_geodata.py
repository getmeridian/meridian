"""Use already-cached, verified Xray archives for explanation; never fetch one here."""

from __future__ import annotations

import hashlib
import zipfile
from functools import lru_cache
from pathlib import Path

from meridian.core.delivery_geodata import GeoData
from meridian.core.xray_pins import XRAY_SHA256

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_GEODATA_BYTES = 64 * 1024 * 1024


def cached_geodata(directory: Path) -> GeoData | None:
    for digest in XRAY_SHA256.values():
        path = directory / digest
        try:
            stat = path.stat()
            if stat.st_size <= MAX_ARCHIVE_BYTES:
                result = read_geodata_archive(path, digest, stat.st_mtime_ns, stat.st_size)
                if result is not None:
                    return result
        except OSError:
            continue
    return None


@lru_cache(maxsize=4)
def read_geodata_archive(path: Path, digest: str, modified: int, size: int) -> GeoData | None:
    # Only archive identity is cached. Destinations and match results never are.
    del modified
    if digest not in XRAY_SHA256.values() or size > MAX_ARCHIVE_BYTES:
        return None
    try:
        with path.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != digest:
                return None
            handle.seek(0)
            with zipfile.ZipFile(handle) as archive:
                names = ("geosite.dat", "geoip.dat")
                if any(archive.getinfo(name).file_size > MAX_GEODATA_BYTES for name in names):
                    return None
                return GeoData.from_bytes(archive.read(names[0]), archive.read(names[1]))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
