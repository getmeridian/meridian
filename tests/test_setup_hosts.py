"""Tests for host creation and inbound caching in setup — create_hosts_for_node, cache_inbounds.

Verifies idempotency (skip by remark), partial failure (warn and continue),
missing inbounds, and the tag → ProtocolKey mapping.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from meridian.cluster import ClusterConfig, InboundRef, NodeEntry, PanelConfig, ProtocolKey
from meridian.core.errors import PanelSetupError
from meridian.node_deploy import register_or_reuse_node
from meridian.panel_bootstrap import cache_inbounds, create_hosts_for_node
from meridian.remnawave import Host, MeridianPanel, Node, RemnawaveError

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
    panel.update_host = MagicMock(return_value=MagicMock(uuid="host-uuid"))
    panel.list_inbounds = MagicMock(return_value=[])
    return panel


def _configured_cluster() -> ClusterConfig:
    """Cluster with all three inbound protocols cached."""
    return ClusterConfig(
        config_profile_uuid="profile-uuid",
        panel=PanelConfig(url="https://198.51.100.1/panel", api_token="test-token"),
        nodes=[NodeEntry(ip=_IP, xhttp_path="xhttp-path", ws_path="ws-path", hysteria2=False)],
        inbounds={
            ProtocolKey.REALITY: InboundRef(uuid="ib-reality-uuid", tag="vless-reality"),
            ProtocolKey.XHTTP: InboundRef(uuid="ib-xhttp-uuid", tag="vless-xhttp"),
            ProtocolKey.WSS: InboundRef(uuid="ib-wss-uuid", tag="vless-wss"),
        },
    )


def _host_with_remark(remark: str) -> Host:
    """Build the converged Host shape returned by panel.list_hosts()."""
    if remark.startswith("reality-"):
        return Host(
            uuid=f"host-{remark}",
            remark=remark,
            address=_IP,
            port=443,
            sni=_SNI,
            fingerprint="chrome",
            security_layer="DEFAULT",
            config_profile_uuid="profile-uuid",
            inbound_uuid="ib-reality-uuid",
        )
    if remark.startswith("xhttp-"):
        return Host(
            uuid=f"host-{remark}",
            remark=remark,
            address=_DOMAIN,
            port=443,
            sni=_DOMAIN,
            host=_DOMAIN,
            path="/xhttp-path",
            security_layer="TLS",
            config_profile_uuid="profile-uuid",
            inbound_uuid="ib-xhttp-uuid",
        )
    return Host(
        uuid=f"host-{remark}",
        remark=remark,
        address=_DOMAIN,
        port=443,
        sni=_DOMAIN,
        host=_DOMAIN,
        path="/ws-path",
        security_layer="TLS",
        config_profile_uuid="profile-uuid",
        inbound_uuid="ib-wss-uuid",
    )


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
        assert kw["port"] == 443
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
        assert kw["sni"] == _DOMAIN
        assert kw["host_header"] == _DOMAIN
        assert kw["path"] == "/xhttp-path"

    def test_creates_wss_host_only_with_domain(self) -> None:
        panel = _make_panel()
        cluster = _configured_cluster()
        create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        kw = _find_create_call(panel, f"wss-{_DOMAIN}")
        assert kw is not None
        assert kw["address"] == _DOMAIN
        assert kw["port"] == 443
        assert kw["inbound_uuid"] == "ib-wss-uuid"
        assert kw["sni"] == _DOMAIN
        assert kw["host_header"] == _DOMAIN
        assert kw["path"] == "/ws-path"

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
        cluster.nodes[0].ip = other_ip
        create_hosts_for_node(panel, cluster, other_ip, _DOMAIN, _SNI, _REALITY_PORT)

        kw = _find_create_call(panel, f"reality-{other_ip}")
        assert kw is not None
        assert kw["address"] == other_ip

    def test_reality_host_advertises_public_listener_not_backend_port(self) -> None:
        panel = _make_panel()
        cluster = _configured_cluster()
        custom_port = 22345
        create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, custom_port)

        kw = _find_create_call(panel, f"reality-{_IP}")
        assert kw is not None
        assert kw["port"] == 443

    def test_hysteria_host_uses_certificate_name_and_h3(self) -> None:
        panel = _make_panel()
        cluster = _configured_cluster()
        cluster.nodes[0].hysteria2 = True
        cluster.inbounds[ProtocolKey.HYSTERIA2] = InboundRef(
            uuid="ib-hysteria-uuid",
            tag="hysteria2",
        )

        create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        kw = _find_create_call(panel, f"hysteria2-{_DOMAIN}")
        assert kw is not None
        assert kw["address"] == _DOMAIN
        assert kw["port"] == 443
        assert kw["sni"] == _DOMAIN
        assert kw["alpn"] == "h3"


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
        panel.update_host.assert_not_called()

    def test_existing_host_drift_is_updated_in_place(self) -> None:
        panel = _make_panel()
        drifted = _host_with_remark(f"reality-{_IP}")
        drifted.port = _REALITY_PORT
        panel.list_hosts.return_value = [drifted]
        cluster = _configured_cluster()

        create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        panel.update_host.assert_any_call(
            drifted.uuid,
            remark=f"reality-{_IP}",
            address=_IP,
            port=443,
            config_profile_uuid="profile-uuid",
            inbound_uuid="ib-reality-uuid",
            sni=_SNI,
            host_header="",
            path="",
            alpn=None,
            fingerprint="chrome",
            security_layer="DEFAULT",
            is_disabled=False,
        )
        assert _find_create_call(panel, f"reality-{_IP}") is None


# ===========================================================================
# create_hosts_for_node — partial failure
# ===========================================================================


class TestCreateHostsForNodeFailure:
    """Required Host observation and mutation failures abort the apply."""

    def test_reality_host_creation_failure_is_hard(self) -> None:
        panel = _make_panel()

        def _fail_on_reality(**kwargs: object) -> MagicMock:
            if kwargs.get("remark", "").startswith("reality-"):
                raise RemnawaveError("boom")
            return MagicMock(uuid="host-uuid")

        panel.create_host.side_effect = _fail_on_reality
        cluster = _configured_cluster()

        with pytest.raises(PanelSetupError, match="required Host"):
            create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        assert panel.create_host.call_count == 1

    def test_later_host_failure_stops_before_publish_completes(self) -> None:
        panel = _make_panel()

        def _fail_on_xhttp(**kwargs: object) -> MagicMock:
            if kwargs.get("remark", "").startswith("xhttp-"):
                raise RemnawaveError("xhttp down")
            return MagicMock(uuid="host-uuid")

        panel.create_host.side_effect = _fail_on_xhttp
        cluster = _configured_cluster()

        with pytest.raises(PanelSetupError, match="xhttp"):
            create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        assert panel.create_host.call_count == 2

    def test_list_hosts_failure_does_not_assume_empty_remote_state(self) -> None:
        panel = _make_panel()
        panel.list_hosts.side_effect = RemnawaveError("api down")
        cluster = _configured_cluster()

        with pytest.raises(PanelSetupError, match="observe existing"):
            create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        panel.create_host.assert_not_called()


# ===========================================================================
# create_hosts_for_node — missing inbounds
# ===========================================================================


class TestCreateHostsForNodeMissingInbounds:
    """Required profile inbounds must exist before any Host is published."""

    def test_no_reality_inbound_fails_before_mutation(self) -> None:
        panel = _make_panel()
        cluster = _configured_cluster()
        del cluster.inbounds[ProtocolKey.REALITY]

        with pytest.raises(PanelSetupError, match="missing required inbounds: reality"):
            create_hosts_for_node(panel, cluster, _IP, _DOMAIN, _SNI, _REALITY_PORT)

        panel.create_host.assert_not_called()

    def test_empty_inbounds_fail_before_mutation(self) -> None:
        panel = _make_panel()
        cluster = ClusterConfig(
            config_profile_uuid="profile-uuid",
            panel=PanelConfig(url="https://198.51.100.1/panel", api_token="test-token"),
            nodes=[NodeEntry(ip=_IP, hysteria2=False)],
            inbounds={},
        )

        with pytest.raises(PanelSetupError, match="reality, xhttp, wss"):
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

    def test_api_error_is_hard_before_host_reconciliation(self) -> None:
        panel = _make_panel()
        panel.list_inbounds.side_effect = RemnawaveError("unreachable")
        cluster = ClusterConfig()

        with pytest.raises(PanelSetupError, match="required Remnawave inbounds"):
            cache_inbounds(panel, cluster)

        assert len(cluster.inbounds) == 0


class TestRegisterOrReuseNode:
    def test_existing_node_profile_binding_is_reconciled(self) -> None:
        panel = MagicMock()
        panel.find_node_by_address.return_value = Node(uuid="node-1", address=_IP)
        panel.get_node_secret_key.return_value = "secret"
        cluster = _configured_cluster()

        credentials = register_or_reuse_node(panel, cluster, _IP, "exit-a")

        assert credentials.uuid == "node-1"
        panel.bind_node_profile.assert_called_once_with(
            "node-1",
            "profile-uuid",
            ["ib-reality-uuid", "ib-xhttp-uuid", "ib-wss-uuid"],
        )
        panel.create_node.assert_not_called()

    def test_missing_node_secret_is_a_hard_failure(self) -> None:
        panel = MagicMock()
        panel.find_node_by_address.return_value = Node(uuid="node-1", address=_IP)
        panel.get_node_secret_key.return_value = ""
        cluster = _configured_cluster()

        with pytest.raises(PanelSetupError, match="no node secret"):
            register_or_reuse_node(panel, cluster, _IP, "exit-a")


# ===========================================================================
# enforce_host_ordering
# ===========================================================================


class TestEnforceHostOrdering:
    def test_orders_relay_before_direct(self) -> None:
        from meridian.node_deploy import enforce_host_ordering

        hosts = [
            Host(uuid="d1", remark="reality-198.51.100.1"),
            Host(uuid="r1", remark="Relay-msk-reality"),
        ]
        panel = MagicMock()
        panel.list_hosts.return_value = hosts
        enforce_host_ordering(panel)
        # relay should come first
        uuids = panel.reorder_hosts.call_args[0][0]
        assert uuids[0] == "r1"
        assert uuids[1] == "d1"

    def test_orders_reality_before_xhttp_before_wss_before_hysteria2(self) -> None:
        from meridian.node_deploy import enforce_host_ordering

        hosts = [
            Host(uuid="h", remark="hysteria2-198.51.100.1"),
            Host(uuid="w", remark="wss-example.com"),
            Host(uuid="x", remark="xhttp-198.51.100.1"),
            Host(uuid="r", remark="reality-198.51.100.1"),
        ]
        panel = MagicMock()
        panel.list_hosts.return_value = hosts
        enforce_host_ordering(panel)
        uuids = panel.reorder_hosts.call_args[0][0]
        assert uuids == ["r", "x", "w", "h"]

    def test_api_error_does_not_crash(self) -> None:
        from meridian.node_deploy import enforce_host_ordering

        hosts = [Host(uuid="a", remark="reality-1")]
        panel = MagicMock()
        panel.list_hosts.return_value = hosts
        panel.reorder_hosts.side_effect = RemnawaveError("API down")
        # Should not raise — just logs warning
        enforce_host_ordering(panel)
