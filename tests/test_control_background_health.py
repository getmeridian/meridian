"""Background failures stay observable without storing or logging exception text."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import BinaryIO
from unittest.mock import patch

import pytest

from meridian.control.blobs import Artifact, BlobStore
from meridian.control.enrollment import token_sha256
from meridian.control.server import ControlServer, _enforce_forever
from meridian.control.service import ControlService
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID

SECRET_ERROR = "mrd_0123456789ab_do-not-log-this-test-secret"


def task(service: ControlService, name: str) -> dict[str, object]:
    health = service.health().model_dump(mode="json")
    return next(item for item in health["background_tasks"] if item["name"] == name)


def test_enforcement_failures_are_in_health_and_warnings_are_rate_limited(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = [1_000.0]
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0", clock=lambda: now[0])

    class Ticks:
        left = 3

        def wait(self, _seconds: float) -> bool:
            self.left -= 1
            now[0] += 30
            return self.left < 0

    with patch.object(service, "enforce", side_effect=RuntimeError(SECRET_ERROR)), caplog.at_level(logging.WARNING):
        _enforce_forever(service, Ticks())

    assert service.health().status == "degraded"
    failed = task(service, "enforcement")
    assert failed["last_attempt_at"] is not None and failed["last_success_at"] is None
    assert failed["error_code"] == "enforcement_failed"
    assert len(caplog.records) == 1
    assert SECRET_ERROR not in caplog.text and SECRET_ERROR not in service.health().model_dump_json()

    ticks = Ticks()
    ticks.left = 1
    with patch.object(service, "enforce", return_value=[]):
        _enforce_forever(service, ticks)
    assert service.health().status == "ok"
    recovered = task(service, "enforcement")
    assert recovered["last_success_at"] is not None and recovered["error_code"] is None


def test_mirror_failure_and_recovery_reach_the_service_health(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    now = [0.0]
    succeeds = [False]
    payload = b"verified test artifact"
    digest = hashlib.sha256(payload).hexdigest()

    def download(_url: str, handle: BinaryIO, _limit: int) -> None:
        if not succeeds[0]:
            raise RuntimeError(SECRET_ERROR)
        handle.write(payload)

    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    blobs = BlobStore(tmp_path / "blobs", download=download, spawn=lambda run: run(), clock=lambda: now[0])
    server = ControlServer(("127.0.0.1", 0), service, token_sha256(ADMIN_TOKEN), blobs)
    try:
        with caplog.at_level(logging.WARNING):
            blobs.want([Artifact(digest, len(payload), ("https://example.com/artifact",))])
            now[0] = 60
            blobs.missed(digest)
        assert service.health().status == "degraded"
        failed = task(service, "blob_mirror")
        assert failed["last_attempt_at"] is not None and failed["last_success_at"] is None
        assert failed["error_code"] == "blob_mirror_failed"
        assert len(caplog.records) == 1 and SECRET_ERROR not in caplog.text

        succeeds[0] = True
        now[0] = 120
        blobs.missed(digest)
        assert service.health().status == "ok"
        assert task(service, "blob_mirror")["last_success_at"] is not None
        assert task(service, "blob_mirror")["error_code"] is None
    finally:
        server.server_close()


def test_another_successful_blob_does_not_hide_a_still_missing_artifact(tmp_path: Path) -> None:
    bad = Artifact("a" * 64, 5, ("https://example.com/unavailable",))
    payload = b"another verified artifact"
    good = Artifact(hashlib.sha256(payload).hexdigest(), len(payload), ("https://example.com/available",))

    def download(url: str, handle: BinaryIO, _limit: int) -> None:
        if url.endswith("unavailable"):
            raise RuntimeError(SECRET_ERROR)
        handle.write(payload)

    blobs = BlobStore(tmp_path / "blobs", download=download, spawn=lambda run: run())
    blobs.want([bad, good])
    state = blobs.health.state()
    assert state.error_code == "blob_mirror_failed" and state.last_success_at is not None
