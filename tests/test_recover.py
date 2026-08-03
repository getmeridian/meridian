"""Tests for cluster recovery — config metadata extraction from Xray configs.

Verifies that _extract_config_metadata correctly parses Reality keys,
XHTTP paths, and WSS paths from config profile raw data.
"""

from __future__ import annotations

from meridian.commands.recover import _extract_config_metadata

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_PRIVATE_KEY = "WDMnPFb3sIZpJGcNhI8jBh7bSCIhRak-bNWDaeqJe1Y"
_SAMPLE_SHORT_ID = "a1b2c3d4"
_SAMPLE_SNI = "www.microsoft.com"
_SAMPLE_XHTTP_PATH = "xh-abc123"
_SAMPLE_WS_PATH = "ws-def456"


def _make_profile_raw(
    *,
    reality: bool = True,
    xhttp: bool = False,
    wss: bool = False,
    private_key: str = _SAMPLE_PRIVATE_KEY,
    short_id: str = _SAMPLE_SHORT_ID,
    sni: str = _SAMPLE_SNI,
    xhttp_path: str = _SAMPLE_XHTTP_PATH,
    ws_path: str = _SAMPLE_WS_PATH,
) -> dict:
    """Build a minimal config profile _raw dict with the requested inbounds."""
    inbounds = []

    if reality:
        inbounds.append(
            {
                "tag": "vless-reality",
                "protocol": "vless",
                "port": 443,
                "settings": {"clients": [], "decryption": "none"},
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "dest": f"{sni}:443",
                        "serverNames": [sni],
                        "privateKey": private_key,
                        "shortIds": [short_id],
                    },
                },
            }
        )

    if xhttp:
        inbounds.append(
            {
                "tag": "vless-xhttp",
                "protocol": "vless",
                "listen": "127.0.0.1",
                "port": 8443,
                "settings": {"clients": [], "decryption": "none"},
                "streamSettings": {
                    "network": "xhttp",
                    "security": "none",
                    "xhttpSettings": {"path": f"/{xhttp_path}"},
                },
            }
        )

    if wss:
        inbounds.append(
            {
                "tag": "vless-wss",
                "protocol": "vless",
                "listen": "127.0.0.1",
                "port": 8444,
                "settings": {"clients": [], "decryption": "none"},
                "streamSettings": {
                    "network": "ws",
                    "security": "none",
                    "wsSettings": {
                        "path": f"/{ws_path}",
                        "headers": {"Host": "cdn.example.com"},
                    },
                },
            }
        )

    return {
        "uuid": "cp-test-uuid",
        "name": "meridian-default",
        "config": {
            "inbounds": inbounds,
            "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        },
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExtractConfigMetadataFromRealityInbound:
    def test_extracts_private_key(self) -> None:
        raw = _make_profile_raw()
        meta = _extract_config_metadata(raw)
        assert meta["private_key"] == _SAMPLE_PRIVATE_KEY

    def test_extracts_short_id(self) -> None:
        raw = _make_profile_raw()
        meta = _extract_config_metadata(raw)
        assert meta["short_id"] == _SAMPLE_SHORT_ID

    def test_extracts_sni(self) -> None:
        raw = _make_profile_raw()
        meta = _extract_config_metadata(raw)
        assert meta["sni"] == _SAMPLE_SNI

    def test_all_keys_present(self) -> None:
        """All recovery metadata keys must be present in the result."""
        raw = _make_profile_raw()
        meta = _extract_config_metadata(raw)
        assert set(meta.keys()) == {"private_key", "public_key", "short_id", "sni", "xhttp_path", "ws_path"}


class TestExtractConfigMetadataMissingInbound:
    def test_no_reality_inbound_returns_empty_keys(self) -> None:
        """Config with no reality inbound should return empty strings for reality fields."""
        raw = _make_profile_raw(reality=False)
        meta = _extract_config_metadata(raw)
        assert meta["private_key"] == ""
        assert meta["short_id"] == ""
        assert meta["sni"] == ""

    def test_no_inbounds_at_all(self) -> None:
        """Config with empty inbounds list."""
        raw = {"config": {"inbounds": []}}
        meta = _extract_config_metadata(raw)
        assert meta["private_key"] == ""
        assert meta["short_id"] == ""
        assert meta["sni"] == ""

    def test_inbound_without_tag(self) -> None:
        """Inbounds missing tag are skipped gracefully."""
        raw = {
            "config": {
                "inbounds": [
                    {"protocol": "vless", "streamSettings": {}},
                ]
            }
        }
        meta = _extract_config_metadata(raw)
        assert meta["private_key"] == ""


class TestExtractConfigMetadataWithXhttpPath:
    def test_extracts_xhttp_path(self) -> None:
        raw = _make_profile_raw(xhttp=True)
        meta = _extract_config_metadata(raw)
        assert meta["xhttp_path"] == _SAMPLE_XHTTP_PATH

    def test_strips_leading_slash(self) -> None:
        """Path in config has leading slash; extracted path should not."""
        raw = _make_profile_raw(xhttp=True, xhttp_path="test-path")
        meta = _extract_config_metadata(raw)
        assert meta["xhttp_path"] == "test-path"
        assert not meta["xhttp_path"].startswith("/")

    def test_no_xhttp_inbound_returns_empty(self) -> None:
        raw = _make_profile_raw(xhttp=False)
        meta = _extract_config_metadata(raw)
        assert meta["xhttp_path"] == ""

    def test_extracts_ws_path(self) -> None:
        raw = _make_profile_raw(wss=True)
        meta = _extract_config_metadata(raw)
        assert meta["ws_path"] == _SAMPLE_WS_PATH

    def test_ws_path_strips_leading_slash(self) -> None:
        raw = _make_profile_raw(wss=True, ws_path="my-ws")
        meta = _extract_config_metadata(raw)
        assert meta["ws_path"] == "my-ws"


class TestExtractConfigMetadataEmptyConfig:
    def test_none_input(self) -> None:
        meta = _extract_config_metadata(None)  # type: ignore[arg-type]
        assert meta == {
            "private_key": "",
            "public_key": "",
            "short_id": "",
            "sni": "",
            "xhttp_path": "",
            "ws_path": "",
        }

    def test_empty_dict(self) -> None:
        meta = _extract_config_metadata({})
        assert meta["private_key"] == ""
        assert meta["sni"] == ""

    def test_config_key_is_none(self) -> None:
        meta = _extract_config_metadata({"config": None})
        assert meta["private_key"] == ""

    def test_config_key_is_not_dict(self) -> None:
        meta = _extract_config_metadata({"config": "not-a-dict"})
        assert meta["private_key"] == ""

    def test_inbounds_is_not_list(self) -> None:
        meta = _extract_config_metadata({"config": {"inbounds": "not-a-list"}})
        assert meta["private_key"] == ""

    def test_all_protocols_together(self) -> None:
        """Full config with all three inbound types."""
        raw = _make_profile_raw(reality=True, xhttp=True, wss=True)
        meta = _extract_config_metadata(raw)
        assert meta["private_key"] == _SAMPLE_PRIVATE_KEY
        assert meta["short_id"] == _SAMPLE_SHORT_ID
        assert meta["sni"] == _SAMPLE_SNI
        assert meta["xhttp_path"] == _SAMPLE_XHTTP_PATH
        assert meta["ws_path"] == _SAMPLE_WS_PATH
