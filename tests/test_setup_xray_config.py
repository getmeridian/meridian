"""Tests for _build_xray_config — Xray inbound generation.

Covers protocol selection (Reality/XHTTP/WSS), key reuse vs generation,
geo-blocking rules, PQ fingerprint, and return value structure.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import typer

from meridian.panel_bootstrap import build_xray_config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SNI = "www.google.com"
_REALITY_PORT = 10589
_XHTTP_PORT = 30589
_WSS_PORT = 20589
_DOMAIN = "vpn.example.com"

_EXISTING_PRIVATE = "PrivateKeyBase64XYZ123"
_EXISTING_PUBLIC = "PublicKeyBase64ABC789"
_EXISTING_SHORT_ID = "deadbeef"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _call_with_existing_keys(**overrides: object) -> dict:
    """Call _build_xray_config with pre-existing Reality keys (no SSH needed)."""
    defaults: dict = dict(
        conn=None,
        sni=_SNI,
        reality_port=_REALITY_PORT,
        xhttp_port=_XHTTP_PORT,
        wss_port=_WSS_PORT,
        domain="",
        pq=False,
        geo_block=False,
        existing_private_key=_EXISTING_PRIVATE,
        existing_public_key=_EXISTING_PUBLIC,
        existing_short_id=_EXISTING_SHORT_ID,
    )
    defaults.update(overrides)
    return build_xray_config(**defaults)


def _make_keygen_conn() -> MagicMock:
    """Create a mock ServerConnection that returns x25519 keypair."""
    conn = MagicMock()
    result = MagicMock()
    result.returncode = 0
    result.stdout = "Private key: GENERATED_PRIV\nPublic key: GENERATED_PUB\n"
    conn.run.return_value = result
    return conn


def _inbound_tags(result: dict) -> list[str]:
    """Extract inbound tags from config result."""
    return [ib["tag"] for ib in result["config"]["inbounds"]]


# ---------------------------------------------------------------------------
# Inbound selection
# ---------------------------------------------------------------------------


class TestBuildXrayConfigInboundSelection:
    def test_always_includes_reality_inbound(self) -> None:
        result = _call_with_existing_keys()
        assert "vless-reality" in _inbound_tags(result)

    def test_always_includes_xhttp_inbound(self) -> None:
        result = _call_with_existing_keys()
        assert "vless-xhttp" in _inbound_tags(result)

    def test_wss_inbound_only_when_domain_set(self) -> None:
        result = _call_with_existing_keys(domain=_DOMAIN)
        assert "vless-wss" in _inbound_tags(result)

    def test_no_wss_inbound_without_domain(self) -> None:
        result = _call_with_existing_keys(domain="")
        assert "vless-wss" not in _inbound_tags(result)

    def test_inbound_count_no_domain_is_two(self) -> None:
        result = _call_with_existing_keys(domain="")
        assert len(result["config"]["inbounds"]) == 2

    def test_inbound_count_with_domain_is_three(self) -> None:
        result = _call_with_existing_keys(domain=_DOMAIN)
        assert len(result["config"]["inbounds"]) == 3


# ---------------------------------------------------------------------------
# Reality inbound details
# ---------------------------------------------------------------------------


class TestBuildXrayConfigRealityInbound:
    def _reality(self, **kw: object) -> dict:
        result = _call_with_existing_keys(**kw)
        return next(ib for ib in result["config"]["inbounds"] if ib["tag"] == "vless-reality")

    def test_reality_uses_provided_sni(self) -> None:
        ib = self._reality(sni="cdn.example.net")
        assert ib["streamSettings"]["realitySettings"]["serverNames"] == ["cdn.example.net"]
        assert ib["streamSettings"]["realitySettings"]["dest"] == "cdn.example.net:443"

    def test_reality_uses_provided_port(self) -> None:
        ib = self._reality(reality_port=12345)
        assert ib["port"] == 12345

    def test_reality_listens_on_localhost(self) -> None:
        ib = self._reality()
        assert ib["listen"] == "127.0.0.1"

    def test_reality_protocol_is_vless(self) -> None:
        ib = self._reality()
        assert ib["protocol"] == "vless"

    def test_reality_fingerprint_is_chrome(self) -> None:
        ib = self._reality()
        assert ib["streamSettings"]["realitySettings"]["fingerprint"] == "chrome"

    def test_reality_decryption_is_none(self) -> None:
        ib = self._reality()
        assert ib["settings"]["decryption"] == "none"

    def test_reality_clients_empty(self) -> None:
        ib = self._reality()
        assert ib["settings"]["clients"] == []

    def test_reality_network_is_tcp(self) -> None:
        ib = self._reality()
        assert ib["streamSettings"]["network"] == "tcp"

    def test_reality_security_is_reality(self) -> None:
        ib = self._reality()
        assert ib["streamSettings"]["security"] == "reality"


# ---------------------------------------------------------------------------
# XHTTP inbound details
# ---------------------------------------------------------------------------


class TestBuildXrayConfigXhttpInbound:
    def _xhttp(self, **kw: object) -> dict:
        result = _call_with_existing_keys(**kw)
        return next(ib for ib in result["config"]["inbounds"] if ib["tag"] == "vless-xhttp")

    def test_xhttp_uses_provided_port(self) -> None:
        ib = self._xhttp(xhttp_port=44444)
        assert ib["port"] == 44444

    def test_xhttp_listens_on_localhost(self) -> None:
        ib = self._xhttp()
        assert ib["listen"] == "127.0.0.1"

    def test_xhttp_path_set_when_provided(self) -> None:
        ib = self._xhttp(xhttp_path="my_xhttp_path")
        assert ib["streamSettings"]["xhttpSettings"]["path"] == "/my_xhttp_path"

    def test_xhttp_no_path_settings_when_empty(self) -> None:
        ib = self._xhttp(xhttp_path="")
        assert "xhttpSettings" not in ib["streamSettings"]

    def test_xhttp_network_is_xhttp(self) -> None:
        ib = self._xhttp()
        assert ib["streamSettings"]["network"] == "xhttp"

    def test_xhttp_security_is_none(self) -> None:
        ib = self._xhttp()
        assert ib["streamSettings"]["security"] == "none"


# ---------------------------------------------------------------------------
# WSS inbound details
# ---------------------------------------------------------------------------


class TestBuildXrayConfigWssInbound:
    def _wss(self, **kw: object) -> dict:
        defaults = dict(domain=_DOMAIN)
        defaults.update(kw)
        result = _call_with_existing_keys(**defaults)
        return next(ib for ib in result["config"]["inbounds"] if ib["tag"] == "vless-wss")

    def test_wss_uses_provided_port(self) -> None:
        ib = self._wss(wss_port=55555)
        assert ib["port"] == 55555

    def test_wss_listens_on_localhost(self) -> None:
        ib = self._wss()
        assert ib["listen"] == "127.0.0.1"

    def test_wss_path_set_when_provided(self) -> None:
        ib = self._wss(ws_path="my_ws_path")
        assert ib["streamSettings"]["wsSettings"]["path"] == "/my_ws_path"

    def test_wss_no_path_settings_when_empty(self) -> None:
        ib = self._wss(ws_path="")
        assert "wsSettings" not in ib["streamSettings"]

    def test_wss_network_is_ws(self) -> None:
        ib = self._wss()
        assert ib["streamSettings"]["network"] == "ws"


# ---------------------------------------------------------------------------
# Key reuse vs generation
# ---------------------------------------------------------------------------


class TestBuildXrayConfigKeyReuse:
    def test_reuses_existing_private_key(self) -> None:
        result = _call_with_existing_keys()
        reality = next(ib for ib in result["config"]["inbounds"] if ib["tag"] == "vless-reality")
        assert reality["streamSettings"]["realitySettings"]["privateKey"] == _EXISTING_PRIVATE

    def test_reuses_existing_short_id(self) -> None:
        result = _call_with_existing_keys()
        reality = next(ib for ib in result["config"]["inbounds"] if ib["tag"] == "vless-reality")
        assert reality["streamSettings"]["realitySettings"]["shortIds"] == [_EXISTING_SHORT_ID]

    def test_returns_existing_public_key(self) -> None:
        result = _call_with_existing_keys()
        assert result["reality_public_key"] == _EXISTING_PUBLIC

    def test_returns_existing_private_key(self) -> None:
        result = _call_with_existing_keys()
        assert result["reality_private_key"] == _EXISTING_PRIVATE

    def test_returns_existing_short_id(self) -> None:
        result = _call_with_existing_keys()
        assert result["reality_short_id"] == _EXISTING_SHORT_ID

    def test_conn_not_needed_when_keys_provided(self) -> None:
        """conn=None is valid when all existing keys are provided."""
        result = _call_with_existing_keys(conn=None)
        assert result["reality_public_key"] == _EXISTING_PUBLIC

    def test_generates_new_keys_when_none_provided(self) -> None:
        conn = _make_keygen_conn()
        result = build_xray_config(
            conn=conn,
            sni=_SNI,
            reality_port=_REALITY_PORT,
            xhttp_port=_XHTTP_PORT,
            wss_port=_WSS_PORT,
            domain="",
            pq=False,
            geo_block=False,
        )
        assert result["reality_public_key"] == "GENERATED_PUB"
        assert result["reality_private_key"] == "GENERATED_PRIV"
        assert len(result["reality_short_id"]) == 8  # secrets.token_hex(4)

    def test_fails_without_conn_when_keys_missing(self) -> None:
        with pytest.raises(typer.Exit):
            build_xray_config(
                conn=None,
                sni=_SNI,
                reality_port=_REALITY_PORT,
                xhttp_port=_XHTTP_PORT,
                wss_port=_WSS_PORT,
                domain="",
                pq=False,
                geo_block=False,
            )

    def test_partial_keys_trigger_generation(self) -> None:
        """If only some keys are provided, generates fresh set."""
        conn = _make_keygen_conn()
        result = build_xray_config(
            conn=conn,
            sni=_SNI,
            reality_port=_REALITY_PORT,
            xhttp_port=_XHTTP_PORT,
            wss_port=_WSS_PORT,
            domain="",
            pq=False,
            geo_block=False,
            existing_private_key=_EXISTING_PRIVATE,
            existing_public_key="",  # missing
            existing_short_id=_EXISTING_SHORT_ID,
        )
        # Should generate because not all three present
        assert result["reality_public_key"] == "GENERATED_PUB"


# ---------------------------------------------------------------------------
# Geo-blocking rules
# ---------------------------------------------------------------------------


class TestBuildXrayConfigGeoBlocking:
    def _routing_rules(self, **kw: object) -> list[dict]:
        result = _call_with_existing_keys(**kw)
        return result["config"]["routing"]["rules"]

    def test_geo_block_enabled_includes_ru_domain_rule(self) -> None:
        rules = self._routing_rules(geo_block=True)
        domain_rules = [r for r in rules if "domain" in r]
        assert any("geosite:category-ru" in r["domain"] for r in domain_rules)

    def test_geo_block_enabled_includes_ru_ip_rule(self) -> None:
        rules = self._routing_rules(geo_block=True)
        ip_rules = [r for r in rules if "ip" in r]
        assert any("geoip:ru" in r["ip"] for r in ip_rules)

    def test_geo_block_disabled_excludes_ru_rules(self) -> None:
        rules = self._routing_rules(geo_block=False)
        domain_rules = [r for r in rules if "domain" in r]
        assert not domain_rules

    def test_private_ip_block_always_present(self) -> None:
        rules = self._routing_rules(geo_block=False)
        ip_rules = [r for r in rules if "ip" in r]
        assert any("geoip:private" in r["ip"] for r in ip_rules)

    def test_private_ip_block_present_with_geo_block(self) -> None:
        rules = self._routing_rules(geo_block=True)
        ip_rules = [r for r in rules if "ip" in r]
        assert any("geoip:private" in r["ip"] for r in ip_rules)

    def test_all_blocked_rules_use_block_outbound(self) -> None:
        rules = self._routing_rules(geo_block=True)
        for rule in rules:
            assert rule["outboundTag"] == "block"


# ---------------------------------------------------------------------------
# Outbounds and base config
# ---------------------------------------------------------------------------


class TestBuildXrayConfigBaseConfig:
    def test_has_direct_outbound(self) -> None:
        result = _call_with_existing_keys()
        tags = [o["tag"] for o in result["config"]["outbounds"]]
        assert "direct" in tags

    def test_has_block_outbound(self) -> None:
        result = _call_with_existing_keys()
        tags = [o["tag"] for o in result["config"]["outbounds"]]
        assert "block" in tags

    def test_has_dns_config(self) -> None:
        result = _call_with_existing_keys()
        assert "dns" in result["config"]
        assert "servers" in result["config"]["dns"]

    def test_log_level_is_warning(self) -> None:
        result = _call_with_existing_keys()
        assert result["config"]["log"]["loglevel"] == "warning"

    def test_routing_strategy(self) -> None:
        result = _call_with_existing_keys()
        assert result["config"]["routing"]["domainStrategy"] == "IPIfNonMatch"


# ---------------------------------------------------------------------------
# Return value structure
# ---------------------------------------------------------------------------


class TestBuildXrayConfigReturnValue:
    def test_returns_config_key(self) -> None:
        result = _call_with_existing_keys()
        assert "config" in result
        assert isinstance(result["config"], dict)

    def test_returns_reality_public_key(self) -> None:
        result = _call_with_existing_keys()
        assert "reality_public_key" in result

    def test_returns_reality_short_id(self) -> None:
        result = _call_with_existing_keys()
        assert "reality_short_id" in result

    def test_returns_reality_private_key(self) -> None:
        result = _call_with_existing_keys()
        assert "reality_private_key" in result

    def test_return_value_has_exactly_four_keys(self) -> None:
        result = _call_with_existing_keys()
        assert set(result.keys()) == {"config", "reality_public_key", "reality_short_id", "reality_private_key"}
