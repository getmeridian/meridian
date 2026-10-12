"""Stage-timed TLS flow probe against misbehaving local servers."""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.diagnostics.tls_probe import http_get_request, probe_tls_flow
from tests.support.tls_server import closed_port, self_signed_cert, tls_server


@pytest.fixture(scope="module")
def cert_pair(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    return self_signed_cert(tmp_path_factory.mktemp("tls"))


def test_handshake_only_probe_completes_without_sending_a_request(cert_pair: tuple[Path, Path]) -> None:
    with tls_server(*cert_pair) as port:
        result = probe_tls_flow("127.0.0.1", "example.org", port=port, timeout=3)
    assert result.stage == "complete"
    assert result.connect_ms is not None and result.handshake_ms is not None
    assert result.transfer_ms is None and result.bytes_received == 0
    assert result.server_answered_tls


def test_full_transfer_reads_until_eof_and_parses_the_status(cert_pair: tuple[Path, Path]) -> None:
    with tls_server(*cert_pair, body_bytes=70 * 1024) as port:
        result = probe_tls_flow(
            "127.0.0.1", "", port=port, request=http_get_request("/asset.js", "127.0.0.1"), timeout=3
        )
    assert result.stage == "complete"
    assert result.http_status == 200
    assert result.bytes_received > 70 * 1024
    assert result.first_byte_ms is not None and not result.stalled


def test_stall_after_sixteen_kilobytes_is_reported_as_a_frozen_transfer(cert_pair: tuple[Path, Path]) -> None:
    with tls_server(*cert_pair, behavior="stall", stall_after=16 * 1024) as port:
        result = probe_tls_flow(
            "127.0.0.1",
            "",
            port=port,
            request=http_get_request("/asset.js", "127.0.0.1"),
            timeout=3,
            stall_timeout=0.5,
        )
    assert result.stage == "transfer"
    assert result.stalled
    assert 15 * 1024 <= result.bytes_received <= 16 * 1024
    assert "froze after 16.0 KB" in result.describe()


def test_silent_listener_fails_at_handshake_with_timeout(cert_pair: tuple[Path, Path]) -> None:
    with tls_server(*cert_pair, behavior="silent") as port:
        result = probe_tls_flow("127.0.0.1", "example.org", port=port, timeout=0.5)
    assert result.stage == "handshake"
    assert result.error_kind == "timeout"
    assert result.silenced and not result.server_answered_tls


def test_reset_during_handshake_is_a_silenced_flow(cert_pair: tuple[Path, Path]) -> None:
    with tls_server(*cert_pair, behavior="reset") as port:
        result = probe_tls_flow("127.0.0.1", "example.org", port=port, timeout=2)
    assert result.stage == "handshake"
    assert result.error_kind in {"reset", "closed"}
    assert result.silenced


def test_tls_alert_proves_the_server_answered(cert_pair: tuple[Path, Path]) -> None:
    with tls_server(*cert_pair, behavior="alert") as port:
        result = probe_tls_flow("127.0.0.1", "blocked.example", port=port, timeout=2)
    assert result.stage == "handshake"
    assert result.error_kind == "tls_alert"
    assert result.server_answered_tls and not result.silenced


def test_closed_port_fails_at_connect_as_refused() -> None:
    result = probe_tls_flow("127.0.0.1", "", port=closed_port(), timeout=1)
    assert result.stage == "connect"
    assert result.error_kind == "refused"
    assert not result.silenced


def test_helper_failure_is_unavailable_not_negative_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("meridian.diagnostics.tls_probe.run_json_helper", lambda *_a, **_k: None)
    result = probe_tls_flow("198.51.100.7", "example.org")
    assert result.stage == "unavailable"
    assert result.describe() == "the local probe could not run"
