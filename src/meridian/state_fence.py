"""The control server's apply fence, stored as one root-only JSON file.

Writes are compare-and-swap: the new record is uploaded beside the old one and
moved into place under ``flock`` only if the old file's SHA-256 still matches
what this process read. Decisions live in ``meridian.reconciler.fence``.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shlex
import socket
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime

from meridian.reconciler.fence import (
    RENEW_AFTER,
    FenceRecord,
    FleetFenceError,
    LocalFenceView,
    committed,
    decide_acquire,
    released,
    renewed,
)
from meridian.ssh import ServerConnection

FENCE_DIR = "/var/lib/meridian"
FENCE_FILE = f"{FENCE_DIR}/fence.json"
_ABSENT = "absent"
_CONFLICT_RC = 4


def apply_holder() -> str:
    """Who holds the lease: host, machine node, and process, shown to a refused machine."""
    return f"{_machine()}:{os.getpid()}:{secrets.token_hex(3)}"


def holder_gone(holder: str) -> bool:
    """True when *holder* was a process on this machine that no longer runs."""
    parts = holder.split(":")
    if len(parts) != 4 or ":".join(parts[:2]) != _machine():
        return False
    try:
        os.kill(int(parts[2]), 0)
    except ProcessLookupError:
        return True
    except (PermissionError, ValueError, OSError):
        return False
    return False


def _machine() -> str:
    return f"{socket.gethostname() or 'unknown'}:{uuid.getnode():012x}"


class SSHApplyFence:
    """``ApplyFence`` backed by ``/var/lib/meridian/fence.json`` on the control server."""

    def __init__(
        self,
        conn: ServerConnection,
        *,
        holder: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._conn = conn
        self._holder = holder or apply_holder()
        self._now = clock or (lambda: datetime.now(UTC))
        self._record: FenceRecord | None = None
        self._digest = _ABSENT
        self._renewed_at: datetime | None = None

    def acquire(self, local: LocalFenceView) -> str:
        remote, digest = self._read()
        record = decide_acquire(
            remote,
            local,
            holder=self._holder,
            now=self._now(),
            new_fleet_id=str(uuid.uuid4()),
            holder_gone=holder_gone,
        )
        self._write(record, digest)
        self._renewed_at = self._now()
        return record.fleet_id

    def check(self, local: LocalFenceView) -> str:
        try:
            remote, _digest = self._read()
            decide_acquire(
                remote,
                local,
                holder=self._holder,
                now=self._now(),
                new_fleet_id="",
                holder_gone=holder_gone,
            )
        except FleetFenceError as exc:
            if exc.category != "user":
                return ""  # Unreachable evidence is reported by the observations themselves.
            return f"{exc} {exc.hint}".strip()
        return ""

    def renew(self) -> None:
        if self._record is None or self._renewed_at is None:
            return
        now = self._now()
        if now - self._renewed_at < RENEW_AFTER:
            return
        self._write(renewed(self._record, now=now), self._digest)
        self._renewed_at = now

    def commit(self, generation: int, plan_hash: str) -> None:
        if self._record is None:
            return
        try:
            self._write(committed(self._record, generation=generation, plan_hash=plan_hash), self._digest)
        except FleetFenceError as exc:
            raise FleetFenceError(
                f"Applied and saved locally, but the control server's record was not updated: {exc}",
                hint="Run the same command again; it changes nothing else and records this generation.",
                category="system",
                retryable=True,
            ) from exc
        self._record = None

    def release(self) -> None:
        if self._record is None:
            return
        try:
            self._write(released(self._record), self._digest)
        except FleetFenceError:
            pass  # An unreleased lease simply expires.
        self._record = None

    def _read(self) -> tuple[FenceRecord | None, str]:
        path, directory = shlex.quote(FENCE_FILE), shlex.quote(FENCE_DIR)
        result = self._conn.run(
            f"mkdir -p {directory} && chmod 700 {directory} && "
            f"if [ -e {path} ]; then sha256sum {path} | cut -c1-64 && cat {path}; else echo {_ABSENT}; fi",
            timeout=20,
            sensitive=True,
        )
        if result.returncode != 0:
            raise FleetFenceError(
                "Could not read the apply fence on the control server.",
                hint=(result.stderr or result.stdout or "SSH to the control server failed.").strip()[:300],
                category="system",
                retryable=True,
            )
        digest, _, body = result.stdout.lstrip().partition("\n")
        digest = digest.strip()
        if digest == _ABSENT:
            return None, _ABSENT
        try:
            data = json.loads(body)
            record = FenceRecord(
                fleet_id=str(data["fleet_id"]),
                generation=int(data.get("generation", 0)),
                plan_hash=str(data.get("plan_hash", "")),
                lease_holder=str(data.get("lease_holder", "")),
                lease_expires_at=str(data.get("lease_expires_at", "")),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise FleetFenceError(
                f"The apply fence on the control server is unreadable ({exc}).",
                hint=f"Inspect {FENCE_FILE} on the control server; deleting it lets the next apply recreate it.",
                category="system",
            ) from exc
        return record, digest

    def _write(self, record: FenceRecord, expected_digest: str) -> None:
        text = json.dumps(asdict(record), indent=2, sort_keys=True) + "\n"
        staged = f"{FENCE_FILE}.{secrets.token_hex(4)}"
        upload = self._conn.put_text(staged, text, mode="600", sensitive=True, operation_name="stage apply fence")
        if upload.returncode != 0:
            raise FleetFenceError(
                "Could not write the apply fence on the control server.",
                hint=(upload.stderr or "SSH to the control server failed.").strip()[:300],
                category="system",
                retryable=True,
            )
        path, staged_q = shlex.quote(FENCE_FILE), shlex.quote(staged)
        swap = (
            f"cur=$(if [ -e {path} ]; then sha256sum {path} | cut -c1-64; else echo {_ABSENT}; fi); "
            f'if [ "$cur" = {shlex.quote(expected_digest)} ]; then mv {staged_q} {path}; '
            f"else rm -f {staged_q}; exit {_CONFLICT_RC}; fi"
        )
        result = self._conn.run(
            f"flock -w 15 {shlex.quote(FENCE_FILE + '.lock')} sh -c {shlex.quote(swap)}",
            timeout=30,
        )
        if result.returncode == _CONFLICT_RC:
            raise FleetFenceError(
                "Another machine changed this fleet's apply fence at the same moment.",
                hint="Copy ~/.meridian from the machine that applied last, then retry.",
                category="user",
            )
        if result.returncode != 0:
            raise FleetFenceError(
                "Could not update the apply fence on the control server.",
                hint=(result.stderr or result.stdout or "flock or mv failed.").strip()[:300],
                category="system",
                retryable=True,
            )
        self._record = record
        self._digest = _sha256(text)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
