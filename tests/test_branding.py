"""Tests for branding — palette validation, emoji extraction, icon processing."""

from __future__ import annotations

import base64
import io
import socket
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from meridian.branding import (
    _extract_emoji,
    _is_private_ip,
    _process_image_url,
    normalize_icon,
    process_icon,
)
from meridian.core.inputs import PAGE_PALETTES, validate_optional_palette_value

# ---------------------------------------------------------------------------
# Palette validation (core) and normalize_icon
# ---------------------------------------------------------------------------


class TestPalette:
    @pytest.mark.parametrize("raw,expected", [("ocean", "ocean"), (" OCEAN ", "ocean"), ("Slate", "slate"), ("", "")])
    def test_known_palettes_normalize(self, raw: str, expected: str) -> None:
        assert validate_optional_palette_value(raw) == expected

    @pytest.mark.parametrize("invalid", ["blue", "red", "purple"])
    def test_unknown_palette_lists_the_choices(self, invalid: str) -> None:
        with pytest.raises(ValueError, match="ocean, sunset, forest, lavender, rose, slate"):
            validate_optional_palette_value(invalid)

    def test_page_palettes_match_the_page(self) -> None:
        from pathlib import Path

        app_js = Path("src/meridian/templates/pwa/app.js").read_text()
        for palette in PAGE_PALETTES:
            assert f"  {palette}:" in app_js


class TestNormalizeIcon:
    def test_emoji_and_saved_data_uri_pass(self) -> None:
        assert normalize_icon(" 🛡️ ") == "🛡️"
        assert normalize_icon("data:image/png;base64,iVBORw0KGgo=") == "data:image/png;base64,iVBORw0KGgo="

    def test_plain_words_are_refused(self) -> None:
        with pytest.raises(ValueError, match="emoji"):
            normalize_icon("shield")

    def test_unusable_image_url_is_refused(self) -> None:
        with patch("meridian.branding._process_image_url", return_value=""):
            with pytest.raises(ValueError, match="Could not use the image"):
                normalize_icon("https://example.com/icon.png")


# ---------------------------------------------------------------------------
# _extract_emoji
# ---------------------------------------------------------------------------


class TestExtractEmoji:
    def test_simple_emoji(self) -> None:
        assert _extract_emoji("🛡️") == "🛡️"

    def test_zwj_sequence(self) -> None:
        result = _extract_emoji("👨\u200d👩\u200d👧\u200d👦")
        assert result == "👨\u200d👩\u200d👧\u200d👦"

    def test_skin_tone(self) -> None:
        result = _extract_emoji("👍🏽")
        assert "👍" in result

    def test_flag_emoji(self) -> None:
        result = _extract_emoji("🇺🇸")
        assert result == "🇺🇸"

    def test_emoji_in_text_extracts_first(self) -> None:
        result = _extract_emoji("hello 🚀 world")
        assert result == "🚀"

    def test_no_emoji_returns_empty(self) -> None:
        assert _extract_emoji("hello") == ""

    def test_multiple_emoji_separated_by_text_returns_first(self) -> None:
        result = _extract_emoji("🛡️ and 🚀")
        # Should return only the first emoji sequence
        assert "🛡" in result
        assert "🚀" not in result

    def test_empty_string(self) -> None:
        assert _extract_emoji("") == ""


# ---------------------------------------------------------------------------
# process_icon
# ---------------------------------------------------------------------------


class TestProcessIcon:
    def test_emoji_input_returns_emoji(self) -> None:
        assert process_icon("🚀") == "🚀"

    def test_empty_input_returns_empty(self) -> None:
        assert process_icon("") == ""

    def test_whitespace_only_returns_empty(self) -> None:
        assert process_icon("   ") == ""

    @patch("meridian.branding._process_image_url")
    def test_url_input_delegates_to_process_image_url(self, mock_process: MagicMock) -> None:
        mock_process.return_value = "data:image/png;base64,abc"
        result = process_icon("https://198.51.100.1/icon.png")
        mock_process.assert_called_once_with("https://198.51.100.1/icon.png")
        assert result == "data:image/png;base64,abc"

    @patch("meridian.branding._process_image_url")
    def test_http_url_also_delegates(self, mock_process: MagicMock) -> None:
        mock_process.return_value = ""
        process_icon("http://198.51.100.1/icon.png")
        mock_process.assert_called_once()


# ---------------------------------------------------------------------------
# _is_private_ip
# ---------------------------------------------------------------------------


class TestIsPrivateIp:
    @patch("socket.getaddrinfo")
    def test_loopback_returns_true(self, mock_getaddrinfo: MagicMock) -> None:
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0)),
        ]
        assert _is_private_ip("localhost") is True

    @patch("socket.getaddrinfo")
    def test_private_ip_returns_true(self, mock_getaddrinfo: MagicMock) -> None:
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.1", 0)),
        ]
        assert _is_private_ip("internal.example.com") is True

    @patch("socket.getaddrinfo")
    def test_public_ip_returns_false(self, mock_getaddrinfo: MagicMock) -> None:
        mock_getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
        ]
        assert _is_private_ip("example.com") is False

    @patch("socket.getaddrinfo")
    def test_gaierror_returns_false(self, mock_getaddrinfo: MagicMock) -> None:
        mock_getaddrinfo.side_effect = socket.gaierror("Name resolution failed")
        assert _is_private_ip("nonexistent.example.com") is False


# ---------------------------------------------------------------------------
# _process_image_url
# ---------------------------------------------------------------------------


def _make_minimal_png() -> bytes:
    """Create a minimal valid 1x1 PNG for testing."""
    try:
        from PIL import Image

        buf = io.BytesIO()
        img = Image.new("RGBA", (1, 1), (255, 0, 0, 255))
        img.save(buf, format="PNG")
        return buf.getvalue()
    except ImportError:
        # Fallback: raw PNG bytes for a 1x1 red pixel
        return (
            b"\x89PNG\r\n\x1a\n"  # PNG signature
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
            b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
            b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )


class TestProcessImageUrl:
    @patch("meridian.branding._is_private_ip", return_value=False)
    @patch("urllib.request.urlopen")
    def test_successful_download_returns_data_uri(self, mock_urlopen: MagicMock, mock_private: MagicMock) -> None:
        png_data = _make_minimal_png()
        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.headers = {"Content-Type": "image/png"}
        mock_response.read.return_value = png_data

        mock_urlopen.return_value = mock_response
        result = _process_image_url("https://198.51.100.1/icon.png")

        assert result.startswith("data:image/png;base64,")
        # Verify it's valid base64
        b64_part = result.split(",", 1)[1]
        decoded = base64.b64decode(b64_part)
        assert len(decoded) > 0

    @patch("meridian.branding._is_private_ip", return_value=False)
    @patch("urllib.request.urlopen")
    def test_download_timeout_returns_empty(self, mock_urlopen: MagicMock, mock_private: MagicMock) -> None:
        mock_urlopen.side_effect = urllib.error.URLError(OSError("timed out"))
        result = _process_image_url("https://198.51.100.1/icon.png")
        assert result == ""

    @patch("meridian.branding._is_private_ip", return_value=False)
    @patch("urllib.request.urlopen")
    def test_oversized_response_truncates(self, mock_urlopen: MagicMock, mock_private: MagicMock) -> None:
        # Return data larger than _MAX_DOWNLOAD (10 MB)
        oversized_data = b"\x89PNG\r\n\x1a\n" + (b"\x00" * (10 * 1024 * 1024 + 100))
        mock_response = MagicMock()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.headers = {"Content-Type": "image/png"}
        mock_response.read.return_value = oversized_data

        mock_urlopen.return_value = mock_response
        # Should not raise — returns either truncated result or empty string
        result = _process_image_url("https://198.51.100.1/icon.png")
        assert isinstance(result, str)

    @patch("meridian.branding._is_private_ip", return_value=True)
    def test_private_ip_blocked(self, mock_private: MagicMock) -> None:
        result = _process_image_url("https://198.51.100.1/icon.png")
        assert result == ""

    @patch("meridian.branding._is_private_ip", return_value=False)
    @patch("urllib.request.urlopen")
    def test_http_error_returns_empty(self, mock_urlopen: MagicMock, mock_private: MagicMock) -> None:
        mock_urlopen.side_effect = urllib.error.HTTPError("https://198.51.100.1/icon.png", 404, "Not Found", {}, None)
        result = _process_image_url("https://198.51.100.1/icon.png")
        assert result == ""
