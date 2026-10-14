"""Tests for connection URL QR rendering."""

from __future__ import annotations

import base64

from meridian.urls import generate_qr_base64, generate_qr_terminal

_URL = "vless://test-uuid@198.51.100.10:443?security=reality#test"


def test_generate_qr_terminal_returns_visible_qr() -> None:
    result = generate_qr_terminal(_URL)

    assert result.strip()
    assert "█" in result


def test_generate_qr_base64_returns_png() -> None:
    result = generate_qr_base64(_URL)

    assert base64.b64decode(result).startswith(b"\x89PNG\r\n\x1a\n")
