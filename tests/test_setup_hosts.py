"""Tests for host creation and inbound caching in setup — create_hosts_for_node, cache_inbounds.

Verifies idempotency (skip by remark), partial failure (warn and continue),
missing inbounds, and the tag → ProtocolKey mapping.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from meridian.cluster import ClusterConfig, InboundRef, NodeEntry, PanelConfig, ProtocolKey
from meridian.panel_bootstrap import cache_inbounds, create_hosts_for_node
from meridian.remnawave import MeridianPanel, RemnawaveError

# ---------------------------------------------------------------------------
# Constants — RFC 5737 IPs only
# ---------------------------------------------------------------------------

_IP = "198.51.100.1"
_SNI = "www.google.com"
_DOMAIN = "vpn.example.com"
_REALITY_PORT = 10589

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_panel() -> MeridianPanel:
    """Create a MeridianPanel with mocked internals (no real HTTP)."""
    with patch.dict("sys.modules", {"httpx": MagicMock()}):
        panel = MeridianPanel.__new__(MeridianPanel)
    panel._base = "https://198.51.100.1/panel"
    panel._token = "test-token"
    panel._timeout = 30
    panel._max_retries = 3
    panel._client = MagicMock()
    # Default: no existing hosts, create_host succeeds
    panel.list_hosts = MagicMock(return_value=[])
    panel.create_host = MagicMock(return_value=MagicMock(uuid="host-uuid"))
    panel.list_inbounds = MagicMock(return_value=[])
    return panel


def _configured_cluster() -> ClusterConfig:
    """Cluster with all three inbound protocols cached."""
    return ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/panel", api_token="test-token"),
        nodes=[NodeEntry(ip=_IP)],
        inbounds={
            ProtocolKey.REALITY: InboundRef(uuid="ib-reality-uuid", tag="vless-reality"),
            ProtocolKey.XHTTP: InboundRef(uuid="ib-xhttp-uuid", tag="vless-xhttp"),
            ProtocolKey.WSS: InboundRef(uuid="ib-wss-uuid", tag="vless-wss"),
        },
    )


def _host_with_remark(remark: str) -> SimpleNamespace:
    """Simulate a Host returned by panel.list_hosts()."""
    return SimpleNamespace(remark=remark)


def _inbound(uuid: str, tag: str) -> SimpleNamespace:
    """Simulate an Inbound returned by panel.list_inbounds()."""
    return SimpleNamespace(uuid=uuid, tag=tag)


def _find_create_call(panel: MeridianPanel, remark: str) -> dict | None:
    """Return kwargs of the create_host call matching *remark*, or None."""
    for c in panel.create_host.call_args_list:
        if c.kwargs.get("remark") == remark:
            return c.kwargs
    return None


# ===========================================================================
# create_hosts_for_node — happy path
# ===========================================================================


class TestCreateHostsForNodeHappyPath:
    """All inbounds present, no existing hosts, no failures."""

    def test_creates_reality_host(self) -> None:
        panel = _make_panel()
        cluster = _configured_cluster()
        create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        kw = _find_create_call(panel, f"reality-{_IP}")
        assert kw is not None
        assert kw["address"] == _IP
        assert kw["port"] == _REALITY_PORT
        assert kw["sni"] == _SNI
        assert kw["inbound_uuid"] == "ib-reality-uuid"

    def test_creates_xhttp_host(self) -> None:
        panel = _make_panel()
        cluster = _configured_cluster()
        create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        kw = _find_create_call(panel, f"xhttp-{_DOMAIN}")
        assert kw is not None
        assert kw["address"] == _DOMAIN
        assert kw["port"] == 443
        assert kw["inbound_uuid"] == "ib-xhttp-uuid"

    def test_creates_wss_host_only_with_domain(self) -> None:
        panel = _make_panel()
        cluster = _configured_cluster()
        create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        kw = _find_create_call(panel, f"wss-{_DOMAIN}")
        assert kw is not None
        assert kw["address"] == _DOMAIN
        assert kw["port"] == 443
        assert kw["inbound_uuid"] == "ib-wss-uuid"

    def test_no_wss_host_without_domain(self) -> None:
        panel = _make_panel()
        cluster = _configured_cluster()
        create_hosts_for_node(panel, cluster, _IP, "", _SNI, _REALITY_PORT)

        assert _find_create_call(panel, f"wss-{_DOMAIN}") is None
        # No WSS call at all
        for c in panel.create_host.call_args_list:
            assert not c.kwargs.get("remark", "").startswith("wss-")

    def test_xhttp_address_uses_domain_when_available(self) -> None:
        panel = _make_panel()
        cluster = _configured_cluster()
        create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        kw = _find_create_call(panel, f"xhttp-{_DOMAIN}")
        assert kw is not None
        assert kw["address"] == _DOMAIN

    def test_xhttp_address_falls_back_to_ip(self) -> None:
        panel = _make_panel()
        cluster = _configured_cluster()
        create_hosts_for_node(panel, cluster, _IP, "", _SNI, _REALITY_PORT)

        kw = _find_create_call(panel, f"xhttp-{_IP}")
        assert kw is not None
        assert kw["address"] == _IP

    def test_reality_host_uses_node_ip_directly(self) -> None:
        panel = _make_panel()
        cluster = _configured_cluster()
        other_ip = "198.51.100.99"
        create_hosts_for_node(panel, cluster, other_ip, _DOMAIN, _SNI, _REALITY_PORT)

        kw = _find_create_call(panel, f"reality-{other_ip}")
        assert kw is not None
        assert kw["address"] == other_ip

    def test_reality_port_matches_parameter(self) -> None:
        panel = _make_panel()
        cluster = _configured_cluster()
        custom_port = 22345
        create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, custom_port)

        kw = _find_create_call(panel, f"reality-{_IP}")
        assert kw is not None
        assert kw["port"] == custom_port


# ===========================================================================
# create_hosts_for_node — idempotency
# ===========================================================================


class TestCreateHostsForNodeIdempotency:
    """Skip hosts whose remark already exists in the panel."""

    def test_skips_reality_host_when_remark_exists(self) -> None:
        panel = _make_panel()
        panel.list_hosts.return_value = [_host_with_remark(f"reality-{_IP}")]
        cluster = _configured_cluster()
        create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        assert _find_create_call(panel, f"reality-{_IP}") is None
        # XHTTP and WSS should still be created
        assert _find_create_call(panel, f"xhttp-{_DOMAIN}") is not None
        assert _find_create_call(panel, f"wss-{_DOMAIN}") is not None

    def test_skips_xhttp_host_when_remark_exists(self) -> None:
        panel = _make_panel()
        panel.list_hosts.return_value = [_host_with_remark(f"xhttp-{_DOMAIN}")]
        cluster = _configured_cluster()
        create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        assert _find_create_call(panel, f"xhttp-{_DOMAIN}") is None
        assert _find_create_call(panel, f"reality-{_IP}") is not None

    def test_skips_wss_host_when_remark_exists(self) -> None:
        panel = _make_panel()
        panel.list_hosts.return_value = [_host_with_remark(f"wss-{_DOMAIN}")]
        cluster = _configured_cluster()
        create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        assert _find_create_call(panel, f"wss-{_DOMAIN}") is None
        assert _find_create_call(panel, f"reality-{_IP}") is not None

    def test_creates_missing_hosts_when_some_exist(self) -> None:
        panel = _make_panel()
        panel.list_hosts.return_value = [
            _host_with_remark(f"reality-{_IP}"),
            _host_with_remark(f"wss-{_DOMAIN}"),
        ]
        cluster = _configured_cluster()
        create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        # Only XHTTP should be created
        assert panel.create_host.call_count == 1
        assert _find_create_call(panel, f"xhttp-{_DOMAIN}") is not None

    def test_all_hosts_existing_creates_none(self) -> None:
        panel = _make_panel()
        panel.list_hosts.return_value = [
            _host_with_remark(f"reality-{_IP}"),
            _host_with_remark(f"xhttp-{_DOMAIN}"),
            _host_with_remark(f"wss-{_DOMAIN}"),
        ]
        cluster = _configured_cluster()
        create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        panel.create_host.assert_not_called()


# ===========================================================================
# create_hosts_for_node — partial failure
# ===========================================================================


class TestCreateHostsForNodePartialFailure:
    """Individual host creation failures warn but don't abort."""

    @patch("meridian.panel_bootstrap.warn")
    def test_reality_host_creation_fails_warns_continues(self, mock_warn: MagicMock) -> None:
        panel = _make_panel()

        def _fail_on_reality(**kwargs: object) -> MagicMock:
            if kwargs.get("remark", "").startswith("reality-"):
                raise RemnawaveError("boom")
            return MagicMock(uuid="host-uuid")

        panel.create_host.side_effect = _fail_on_reality
        cluster = _configured_cluster()
        create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        mock_warn.assert_called()
        # XHTTP and WSS should still be attempted
        assert _find_create_call(panel, f"xhttp-{_DOMAIN}") is not None
        assert _find_create_call(panel, f"wss-{_DOMAIN}") is not None

    @patch("meridian.panel_bootstrap.warn")
    def test_one_of_three_hosts_fail_creates_other_two(self, mock_warn: MagicMock) -> None:
        panel = _make_panel()

        def _fail_on_xhttp(**kwargs: object) -> MagicMock:
            if kwargs.get("remark", "").startswith("xhttp-"):
                raise RemnawaveError("xhttp down")
            return MagicMock(uuid="host-uuid")

        panel.create_host.side_effect = _fail_on_xhttp
        cluster = _configured_cluster()
        create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        # 3 attempts total (reality, xhttp, wss)
        assert panel.create_host.call_count == 3
        mock_warn.assert_called()

    def test_list_hosts_fails_still_attempts_creation(self) -> None:
        panel = _make_panel()
        panel.list_hosts.side_effect = RemnawaveError("api down")
        cluster = _configured_cluster()
        create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        # Should fall back to empty set and attempt all creations
        assert panel.create_host.call_count == 3


# ===========================================================================
# create_hosts_for_node — missing inbounds
# ===========================================================================


class TestCreateHostsForNodeMissingInbounds:
    """No hosts created when the corresponding inbound is absent."""

    def test_no_reality_inbound_skips_reality_host(self) -> None:
        panel = _make_panel()
        cluster = _configured_cluster()
        del cluster.inbounds[ProtocolKey.REALITY]
        create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        assert _find_create_call(panel, f"reality-{_IP}") is None
        # XHTTP and WSS should still be created
        assert _find_create_call(panel, f"xhttp-{_DOMAIN}") is not None
        assert _find_create_call(panel, f"wss-{_DOMAIN}") is not None

    def test_empty_inbounds_creates_no_hosts(self) -> None:
        panel = _make_panel()
        cluster = ClusterConfig(
            panel=PanelConfig(url="https://198.51.100.1/panel", api_token="test-token"),
            nodes=[NodeEntry(ip=_IP)],
            inbounds={},
        )
        create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        panel.create_host.assert_not_called()


# ===========================================================================
# cache_inbounds
# ===========================================================================


class TestCacheInbounds:
    """Tag → ProtocolKey mapping and error handling."""

    def test_caches_reality_inbound(self) -> None:
        panel = _make_panel()
        panel.list_inbounds.return_value = [_inbound("uuid-r", "vless-reality")]
        cluster = ClusterConfig()
        cache_inbounds(panel, cluster)

        assert ProtocolKey.REALITY in cluster.inbounds
        assert cluster.inbounds[ProtocolKey.REALITY].uuid == "uuid-r"
        assert cluster.inbounds[ProtocolKey.REALITY].tag == "vless-reality"

    def test_caches_xhttp_inbound(self) -> None:
        panel = _make_panel()
        panel.list_inbounds.return_value = [_inbound("uuid-x", "vless-xhttp")]
        cluster = ClusterConfig()
        cache_inbounds(panel, cluster)

        assert ProtocolKey.XHTTP in cluster.inbounds
        assert cluster.inbounds[ProtocolKey.XHTTP].uuid == "uuid-x"
        assert cluster.inbounds[ProtocolKey.XHTTP].tag == "vless-xhttp"

    def test_caches_wss_inbound(self) -> None:
        panel = _make_panel()
        panel.list_inbounds.return_value = [_inbound("uuid-w", "vless-wss")]
        cluster = ClusterConfig()
        cache_inbounds(panel, cluster)

        assert ProtocolKey.WSS in cluster.inbounds
        assert cluster.inbounds[ProtocolKey.WSS].uuid == "uuid-w"
        assert cluster.inbounds[ProtocolKey.WSS].tag == "vless-wss"

    def test_all_three_inbounds_cached(self) -> None:
        panel = _make_panel()
        panel.list_inbounds.return_value = [
            _inbound("uuid-r", "vless-reality"),
            _inbound("uuid-x", "vless-xhttp"),
            _inbound("uuid-w", "vless-wss"),
        ]
        cluster = ClusterConfig()
        cache_inbounds(panel, cluster)

        assert len(cluster.inbounds) == 3
        assert cluster.inbounds[ProtocolKey.REALITY].uuid == "uuid-r"
        assert cluster.inbounds[ProtocolKey.XHTTP].uuid == "uuid-x"
        assert cluster.inbounds[ProtocolKey.WSS].uuid == "uuid-w"

    def test_ignores_unknown_tags(self) -> None:
        panel = _make_panel()
        panel.list_inbounds.return_value = [
            _inbound("uuid-r", "vless-reality"),
            _inbound("uuid-unknown", "trojan-tcp"),
            _inbound("uuid-mystery", "shadowsocks"),
        ]
        cluster = ClusterConfig()
        cache_inbounds(panel, cluster)

        assert len(cluster.inbounds) == 1
        assert ProtocolKey.REALITY in cluster.inbounds

    @patch("meridian.panel_bootstrap.warn")
    def test_handles_api_error_gracefully(self, mock_warn: MagicMock) -> None:
        panel = _make_panel()
        panel.list_inbounds.side_effect = RemnawaveError("unreachable")
        cluster = ClusterConfig()
        cache_inbounds(panel, cluster)

        mock_warn.assert_called()
        assert len(cluster.inbounds) == 0
