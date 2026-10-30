"""Content-addressed artifacts agents fetch from `GET /agent/v1/blobs/{sha256}`.

A node spec's `binary_pin` that lists the `service` source names an upstream
https URL too; the service mirrors that artifact into `<state>/blobs/<sha256>`
in a background thread (after each spec PUT, at start, and again when an agent
asks for one still missing), verifying size and SHA-256 before an atomic rename.
Relays and exits that cannot reach the upstream then install from the service.
Only https sources are mirrored, and the session check stays in front of reads.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import httpx

from meridian.control.background_health import TaskHealth
from meridian.control.models import NodeSpec
from meridian.core.agent_desired import BinaryPinResource

SERVICE_SOURCE = "service"
# A failed mirror is retried at most this often when agents keep asking.
RETRY_SECONDS = 60.0
DOWNLOAD_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Artifact:
    """One artifact to mirror: its pinned hash and size and where upstream serves it."""

    sha256: str
    size: int
    urls: tuple[str, ...]


# (url, destination, the most bytes to accept) -> None; raises on any failure.
Download = Callable[[str, BinaryIO, int], None]


def download_https(url: str, destination: BinaryIO, limit: int) -> None:
    """Stream `url` into `destination`, refusing a body larger than `limit`."""
    with httpx.stream("GET", url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT) as response:
        response.raise_for_status()
        written = 0
        for chunk in response.iter_bytes(1 << 16):
            written += len(chunk)
            if written > limit:
                raise ValueError("the download is larger than pinned")
            destination.write(chunk)


def served_hashes(spec: NodeSpec) -> set[str]:
    """Every artifact `spec` lets its agents fetch from the service, mirrored or seeded."""
    return {
        resource.sha256
        for resource in spec.resources
        if isinstance(resource, BinaryPinResource) and SERVICE_SOURCE in resource.sources
    }


def spec_artifacts(spec: NodeSpec) -> list[Artifact]:
    """The artifacts of `spec` its agents may fetch from the service."""
    found: dict[str, Artifact] = {}
    for resource in spec.resources:
        if not isinstance(resource, BinaryPinResource) or SERVICE_SOURCE not in resource.sources:
            continue
        urls = tuple(source for source in resource.sources if source.startswith("https://"))
        if urls:
            found.setdefault(resource.sha256, Artifact(resource.sha256, resource.size, urls))
    return list(found.values())


def fleet_artifacts(read_specs: Callable[[], Iterable[NodeSpec]]) -> Iterable[Artifact]:
    """Read the current fleet when consumed, so a wanted-set refresh reads inside the blob lock."""
    for spec in read_specs():
        yield from spec_artifacts(spec)


def _start_thread(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name="blob-mirror", daemon=True).start()


class BlobStore:
    """A directory of verified artifacts named by SHA-256, filled by one mirror worker."""

    def __init__(
        self,
        root: Path,
        *,
        download: Download = download_https,
        spawn: Callable[[Callable[[], None]], None] = _start_thread,
        clock: Callable[[], float] = time.monotonic,
        health: TaskHealth | None = None,
    ) -> None:
        self.root = root
        self._download = download
        self._spawn = spawn
        self._clock = clock
        self._lock = threading.Lock()
        self._wanted: dict[str, Artifact] = {}
        self._pending: list[str] = []
        self._last_try: dict[str, float] = {}
        self._running = False
        self.health = health or TaskHealth("blob_mirror", "blob_mirror_failed")
        self._failed: set[str] = set()

    def path(self, sha256: str) -> Path | None:
        """The stored file for `sha256`, or None."""
        if not _SHA256.fullmatch(sha256):
            return None
        path = self.root / sha256
        return path if path.is_file() else None

    def put(self, data: bytes) -> str:
        """Store `data` and return its SHA-256."""
        digest = hashlib.sha256(data).hexdigest()
        if self.path(digest) is None:
            self._write(digest, lambda handle: handle.write(data))
        with self._lock:
            self._prune_failed()
        return digest

    def want(self, artifacts: Iterable[Artifact]) -> None:
        """Replace the wanted set with the fleet's current artifacts and mirror any missing ones."""
        with self._lock:
            self._wanted = {artifact.sha256: artifact for artifact in artifacts}
            self._pending = [digest for digest in self._pending if digest in self._wanted]
            self._last_try = {digest: stamp for digest, stamp in self._last_try.items() if digest in self._wanted}
            self._prune_failed()
            for artifact in self._wanted.values():
                if self.path(artifact.sha256) is None and artifact.sha256 not in self._pending:
                    self._pending.append(artifact.sha256)
            start = self._claim_worker()
        if start:
            self._spawn(self._drain)

    def missed(self, sha256: str) -> None:
        """An agent asked for a blob that is not stored: try a wanted one again, at most once a minute."""
        with self._lock:
            last = self._last_try.get(sha256)
            if sha256 not in self._wanted or sha256 in self._pending:
                return
            if last is not None and self._clock() - last < RETRY_SECONDS:
                return
            self._pending.append(sha256)
            start = self._claim_worker()
        if start:
            self._spawn(self._drain)

    def _claim_worker(self) -> bool:
        """Under the lock: whether the caller starts the one worker (spawned after releasing it)."""
        if self._pending and not self._running:
            self._running = True
            return True
        return False

    def _prune_failed(self) -> None:
        """Under the lock: keep failures only for wanted, missing artifacts."""
        self._failed = {digest for digest in self._failed if digest in self._wanted and self.path(digest) is None}
        if not self._failed:
            self.health.clear_error()

    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    self._running = False
                    return
                artifact = self._wanted[self._pending.pop(0)]
                self._last_try[artifact.sha256] = self._clock()
            if self.path(artifact.sha256) is None:
                self._mirror(artifact)

    def _mirror(self, artifact: Artifact) -> bool:
        self.health.attempt()
        for url in artifact.urls:
            try:
                self._write(artifact.sha256, lambda handle: self._download(url, handle, artifact.size), artifact.size)
                with self._lock:
                    self._failed.discard(artifact.sha256)
                    self.health.succeeded(clear_error=not self._failed)
                return True
            except Exception:  # noqa: BLE001 - try the next source; an agent's miss retries later
                continue
        with self._lock:
            self._prune_failed()
            if artifact.sha256 in self._wanted and self.path(artifact.sha256) is None:
                self._failed.add(artifact.sha256)
                self.health.failed()
        return False

    def _write(self, digest: str, fill: Callable[[BinaryIO], object], size: int | None = None) -> None:
        """Fill a private temp file, verify size and hash, then rename it into place."""
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self.root, prefix=".partial-")
        try:
            with os.fdopen(fd, "w+b") as handle:
                fill(handle)
                handle.flush()
                if size is not None and handle.tell() != size:
                    raise ValueError("the download size differs from the pin")
                handle.seek(0)
                if hashlib.file_digest(handle, "sha256").hexdigest() != digest:
                    raise ValueError("the download hash differs from the pin")
                os.fsync(handle.fileno())
            os.chmod(temp, 0o644)
            os.replace(temp, self.root / digest)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
