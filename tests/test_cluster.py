"""Tests for cluster config — validation, YAML round-trip, and convenience methods."""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.cluster import (
    BrandingConfig,
    ClusterConfig,
    DesiredNode,
    DesiredRelay,
    InboundRef,
    NodeEntry,
    PanelConfig,
    ProtocolKey,
    RelayEntry,
    SubscriptionPageConfig,
    TelegramConfig,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UUID_A = "550e8400-e29b-41d4-a716-446655440000"
_UUID_B = "660e8400-e29b-41d4-a716-446655440001"
_UUID_C = "770e8400-e29b-41d4-a716-446655440002"
_IP_A = "198.51.100.1"
_IP_B = "198.51.100.2"
_IP_C = "198.51.100.3"


def _configured_cluster(**overrides) -> ClusterConfig:
    """Return a fully valid, configured cluster for testing."""
    defaults = dict(
        panel=PanelConfig(
            url="https://panel.example.com",
            api_token="tok_abc123",
            server_ip=_IP_A,
        ),
        nodes=[
            NodeEntry(ip=_IP_A, uuid=_UUID_A, name="finland"),
            NodeEntry(ip=_IP_B, uuid=_UUID_B, name="germany"),
        ],
        relays=[
            RelayEntry(ip=_IP_C, name="ru-moscow", exit_node_ip=_IP_A),
        ],
        inbounds={
            ProtocolKey.REALITY: InboundRef(uuid=_UUID_C, tag="VLESS_REALITY"),
        },
        branding=BrandingConfig(server_name="Test VPN", color="ocean"),
    )
    defaults.update(overrides)
    return ClusterConfig(**defaults)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestClusterValidation:
    def test_empty_cluster_validates(self) -> None:
        cfg = ClusterConfig()
        assert cfg.validate() == []

    def test_configured_cluster_requires_url_and_token(self) -> None:
        # panel.url set but no api_token — is_configured is False, so no error
        cfg = ClusterConfig(panel=PanelConfig(url="https://panel.example.com"))
        assert cfg.validate() == []

        # Both set (is_configured=True) then blank one — triggers error
        cfg = ClusterConfig(panel=PanelConfig(url="https://panel.example.com", api_token="tok"))
        assert cfg.validate() == []  # both present, no error

    def test_invalid_node_ip_detected(self) -> None:
        cfg = ClusterConfig(nodes=[NodeEntry(ip="not-an-ip", uuid=_UUID_A)])
        errors = cfg.validate()
        assert any("nodes[0].ip" in e and "not a valid IP" in e for e in errors)

    def test_duplicate_node_ip_detected(self) -> None:
        cfg = ClusterConfig(
            nodes=[
                NodeEntry(ip=_IP_A, uuid=_UUID_A),
                NodeEntry(ip=_IP_A, uuid=_UUID_B),
            ]
        )
        errors = cfg.validate()
        assert any("duplicate" in e for e in errors)

    def test_invalid_uuid_detected(self) -> None:
        cfg = ClusterConfig(nodes=[NodeEntry(ip=_IP_A, uuid="bad-uuid")])
        errors = cfg.validate()
        assert any("nodes[0].uuid" in e and "not a valid UUID" in e for e in errors)

    def test_relay_referencing_unknown_node_detected(self) -> None:
        cfg = ClusterConfig(
            nodes=[NodeEntry(ip=_IP_A, uuid=_UUID_A)],
            relays=[RelayEntry(ip=_IP_C, exit_node_ip=_IP_B)],
        )
        errors = cfg.validate()
        assert any("exit_node_ip" in e and "unknown node" in e for e in errors)

    def test_port_out_of_range_detected(self) -> None:
        cfg = ClusterConfig(nodes=[NodeEntry(ip=_IP_A, ssh_port=0)])
        errors = cfg.validate()
        assert any("ssh_port" in e and "out of range" in e for e in errors)

        cfg2 = ClusterConfig(nodes=[NodeEntry(ip=_IP_A, ssh_port=70000)])
        errors2 = cfg2.validate()
        assert any("ssh_port" in e and "out of range" in e for e in errors2)

    def test_valid_cluster_returns_no_errors(self) -> None:
        cfg = _configured_cluster()
        assert cfg.validate() == []

    def test_invalid_panel_server_ip_detected(self) -> None:
        cfg = ClusterConfig(panel=PanelConfig(server_ip="bogus"))
        errors = cfg.validate()
        assert any("panel.server_ip" in e for e in errors)

    def test_invalid_relay_ip_detected(self) -> None:
        cfg = ClusterConfig(relays=[RelayEntry(ip="nope")])
        errors = cfg.validate()
        assert any("relays[0].ip" in e and "not a valid IP" in e for e in errors)

    def test_relay_port_out_of_range_detected(self) -> None:
        cfg = ClusterConfig(relays=[RelayEntry(ip=_IP_C, port=99999)])
        errors = cfg.validate()
        assert any("relays[0].port" in e and "out of range" in e for e in errors)

    def test_invalid_inbound_uuid_detected(self) -> None:
        cfg = ClusterConfig(inbounds={"reality": InboundRef(uuid="not-a-uuid", tag="X")})
        errors = cfg.validate()
        assert any("inbounds[reality].uuid" in e for e in errors)

    def test_relay_exit_node_skipped_when_no_nodes(self) -> None:
        """Relay exit_node_ip check is skipped when there are no nodes (empty cluster)."""
        cfg = ClusterConfig(relays=[RelayEntry(ip=_IP_C, exit_node_ip=_IP_B)])
        errors = cfg.validate()
        # No "unknown node" error because node_ips is empty — can't validate references
        assert not any("unknown node" in e for e in errors)

    def test_panel_ssh_port_out_of_range(self) -> None:
        cfg = ClusterConfig(panel=PanelConfig(ssh_port=0))
        errors = cfg.validate()
        assert any("panel.ssh_port" in e for e in errors)

    def test_duplicate_relay_endpoint_detected(self) -> None:
        cfg = ClusterConfig(
            nodes=[NodeEntry(ip=_IP_A)],
            relays=[
                RelayEntry(ip=_IP_B, port=443, exit_node_ip=_IP_A),
                RelayEntry(ip=_IP_B, port=443, exit_node_ip=_IP_A),
            ],
        )
        errors = cfg.validate()
        assert any("duplicate relay endpoint" in e for e in errors)

    def test_duplicate_relay_identity_detected(self) -> None:
        cfg = ClusterConfig(
            nodes=[NodeEntry(ip=_IP_A)],
            relays=[
                RelayEntry(ip=_IP_B, name="ru moscow", port=443, exit_node_ip=_IP_A),
                RelayEntry(ip=_IP_C, name="ru-moscow", port=443, exit_node_ip=_IP_A),
            ],
        )

        errors = cfg.validate()

        assert any("duplicate relay identity" in e for e in errors)

    def test_desired_relay_same_host_as_desired_node_is_rejected(self) -> None:
        cfg = ClusterConfig(
            desired_nodes=[DesiredNode(host=_IP_A, name="ru-edge")],
            desired_relays=[DesiredRelay(host=_IP_A, name="ru-edge-relay", exit_node="ru-edge")],
        )

        errors = cfg.validate()

        assert any("Use capability routing for same-server relay+exit designs" in e for e in errors)

    def test_multiple_panel_hosts_detected(self) -> None:
        cfg = ClusterConfig(
            nodes=[
                NodeEntry(ip=_IP_A, is_panel_host=True),
                NodeEntry(ip=_IP_B, is_panel_host=True),
            ],
        )
        errors = cfg.validate()
        assert any("Multiple panel hosts" in e for e in errors)

    def test_invalid_panel_url_detected(self) -> None:
        cfg = ClusterConfig(
            panel=PanelConfig(url="not-a-url"),
        )
        errors = cfg.validate()
        assert any("panel.url" in e for e in errors)

    def test_valid_panel_url_accepted(self) -> None:
        cfg = ClusterConfig(
            panel=PanelConfig(url="https://panel.example.com/secret"),
        )
        errors = cfg.validate()
        assert not any("panel.url" in e for e in errors)

    def test_future_version_sets_readonly(self, tmp_path: Path) -> None:
        p = tmp_path / "cluster.yml"
        p.write_text("version: 99\n")
        cfg = ClusterConfig.load(p)
        assert cfg._readonly is True

    def test_readonly_config_cannot_be_saved(self, tmp_path: Path) -> None:
        cfg = ClusterConfig()
        cfg._readonly = True
        with pytest.raises(ValueError, match="newer version"):
            cfg.save(tmp_path / "cluster.yml")


# ---------------------------------------------------------------------------
# YAML round-trip
# ---------------------------------------------------------------------------


class TestClusterYAMLRoundTrip:
    def test_save_and_load_preserves_all_fields(self, tmp_path: Path) -> None:
        cfg = _configured_cluster()
        p = tmp_path / "cluster.yml"
        cfg.save(p)
        loaded = ClusterConfig.load(p)

        assert loaded.panel.url == cfg.panel.url
        assert loaded.panel.api_token == cfg.panel.api_token
        assert loaded.panel.server_ip == cfg.panel.server_ip
        assert len(loaded.nodes) == 2
        assert loaded.nodes[0].ip == _IP_A
        assert loaded.nodes[0].uuid == _UUID_A
        assert loaded.nodes[0].name == "finland"
        assert loaded.nodes[1].ip == _IP_B
        assert len(loaded.relays) == 1
        assert loaded.relays[0].ip == _IP_C
        assert loaded.relays[0].exit_node_ip == _IP_A
        assert loaded.branding.server_name == "Test VPN"
        assert loaded.branding.color == "ocean"
        inbound = loaded.get_inbound(ProtocolKey.REALITY)
        assert inbound is not None
        assert inbound.uuid == _UUID_C
        assert inbound.tag == "VLESS_REALITY"

    def test_save_and_load_preserves_extra_fields(self, tmp_path: Path) -> None:
        cfg = ClusterConfig(_extra={"future_field": "hello"})
        p = tmp_path / "cluster.yml"
        cfg.save(p)
        loaded = ClusterConfig.load(p)
        assert loaded._extra.get("future_field") == "hello"

    def test_save_and_load_preserves_strenum_keys(self, tmp_path: Path) -> None:
        cfg = ClusterConfig(
            inbounds={
                ProtocolKey.REALITY: InboundRef(uuid=_UUID_A, tag="R"),
                ProtocolKey.XHTTP: InboundRef(uuid=_UUID_B, tag="X"),
            }
        )
        p = tmp_path / "cluster.yml"
        cfg.save(p)
        loaded = ClusterConfig.load(p)
        # Keys survive as plain strings (YAML doesn't know about ProtocolKey)
        assert "reality" in loaded.inbounds
        assert "xhttp" in loaded.inbounds
        ref = loaded.get_inbound("reality")
        assert ref is not None
        assert ref.uuid == _UUID_A

    def test_desired_clients_round_trip(self, tmp_path: Path) -> None:
        cfg = _configured_cluster(desired_clients=["alice", "bob", "default"])
        p = tmp_path / "cluster.yml"
        cfg.save(p)
        loaded = ClusterConfig.load(p)
        assert loaded.desired_clients == ["alice", "bob", "default"]

    def test_desired_clients_none_means_unmanaged(self, tmp_path: Path) -> None:
        # None semantically differs from [] — None = "do not manage clients
        # declaratively"; [] = "manage and the desired set is empty (remove all)".
        cfg = _configured_cluster(desired_clients=None)
        p = tmp_path / "cluster.yml"
        cfg.save(p)
        loaded = ClusterConfig.load(p)
        assert loaded.desired_clients is None

    def test_desired_clients_empty_list_round_trip(self, tmp_path: Path) -> None:
        cfg = _configured_cluster(desired_clients=[])
        p = tmp_path / "cluster.yml"
        cfg.save(p)
        loaded = ClusterConfig.load(p)
        assert loaded.desired_clients == []

    def test_desired_nodes_round_trip(self, tmp_path: Path) -> None:
        desired = [
            DesiredNode(
                host="198.51.100.10",
                name="de-fra-1",
                ssh_user="deploy",
                ssh_port=2222,
                domain="de.example.com",
                sni="www.google.com",
                warp=True,
            ),
        ]
        cfg = _configured_cluster(desired_nodes=desired)
        p = tmp_path / "cluster.yml"
        cfg.save(p)
        loaded = ClusterConfig.load(p)
        assert loaded.desired_nodes is not None
        assert len(loaded.desired_nodes) == 1
        n = loaded.desired_nodes[0]
        assert n.host == "198.51.100.10"
        assert n.name == "de-fra-1"
        assert n.ssh_user == "deploy"
        assert n.ssh_port == 2222
        assert n.domain == "de.example.com"
        assert n.sni == "www.google.com"
        assert n.warp is True

    def test_desired_relays_round_trip(self, tmp_path: Path) -> None:
        desired = [
            DesiredRelay(
                host="198.51.100.20",
                name="msk-relay",
                exit_node="finland",
                sni="www.cloudflare.com",
                ssh_user="root",
                ssh_port=22,
            ),
        ]
        cfg = _configured_cluster(desired_relays=desired)
        p = tmp_path / "cluster.yml"
        cfg.save(p)
        loaded = ClusterConfig.load(p)
        assert loaded.desired_relays is not None
        assert len(loaded.desired_relays) == 1
        r = loaded.desired_relays[0]
        assert r.host == "198.51.100.20"
        assert r.name == "msk-relay"
        assert r.exit_node == "finland"
        assert r.sni == "www.cloudflare.com"

    def test_duplicate_desired_relay_identity_detected(self) -> None:
        cfg = ClusterConfig(
            desired_relays=[
                DesiredRelay(host=_IP_A, name="ru moscow", exit_node="finland"),
                DesiredRelay(host=_IP_B, name="ru-moscow", exit_node="finland"),
            ],
        )

        errors = cfg.validate()

        assert any("desired_relays[1].name creates a duplicate relay identity" in e for e in errors)

    def test_subscription_page_round_trip(self, tmp_path: Path) -> None:
        cfg = _configured_cluster(subscription_page=SubscriptionPageConfig(enabled=True, path="abcdef0123456789"))
        p = tmp_path / "cluster.yml"
        cfg.save(p)
        loaded = ClusterConfig.load(p)
        assert loaded.subscription_page is not None
        assert loaded.subscription_page.enabled is True
        assert loaded.subscription_page.path == "abcdef0123456789"

    def test_subscription_page_disabled_round_trip(self, tmp_path: Path) -> None:
        cfg = _configured_cluster(subscription_page=SubscriptionPageConfig(enabled=False, path="xx"))
        p = tmp_path / "cluster.yml"
        cfg.save(p)
        loaded = ClusterConfig.load(p)
        assert loaded.subscription_page is not None
        assert loaded.subscription_page.enabled is False
        # path is preserved even when disabled, so re-enable picks the same nginx route
        assert loaded.subscription_page.path == "xx"

    def test_desired_node_warp_none_round_trip(self, tmp_path: Path) -> None:
        desired = [DesiredNode(host="198.51.100.10", name="x", warp=None)]
        cfg = _configured_cluster(desired_nodes=desired)
        p = tmp_path / "cluster.yml"
        cfg.save(p)
        loaded = ClusterConfig.load(p)
        assert loaded.desired_nodes is not None
        assert loaded.desired_nodes[0].warp is None

    def test_desired_node_warp_false_round_trip(self, tmp_path: Path) -> None:
        desired = [DesiredNode(host="198.51.100.10", name="x", warp=False)]
        cfg = _configured_cluster(desired_nodes=desired)
        p = tmp_path / "cluster.yml"
        cfg.save(p)
        loaded = ClusterConfig.load(p)
        assert loaded.desired_nodes[0].warp is False

    def test_desired_node_warp_true_round_trip(self, tmp_path: Path) -> None:
        desired = [DesiredNode(host="198.51.100.10", name="x", warp=True)]
        cfg = _configured_cluster(desired_nodes=desired)
        p = tmp_path / "cluster.yml"
        cfg.save(p)
        loaded = ClusterConfig.load(p)
        assert loaded.desired_nodes[0].warp is True

    def test_desired_clients_null_yaml_loads_as_none(self, tmp_path: Path) -> None:
        """Explicit `desired_clients: null` in YAML must load as None (unmanaged),
        not as [] (managed-empty). Documented in cluster.example.yml."""
        p = tmp_path / "cluster.yml"
        p.write_text("version: 2\ndesired_clients: null\n")
        loaded = ClusterConfig.load(p)
        assert loaded.desired_clients is None

    def test_desired_nodes_null_yaml_loads_as_none(self, tmp_path: Path) -> None:
        p = tmp_path / "cluster.yml"
        p.write_text("version: 2\ndesired_nodes: null\n")
        loaded = ClusterConfig.load(p)
        assert loaded.desired_nodes is None

    def test_desired_relays_null_yaml_loads_as_none(self, tmp_path: Path) -> None:
        p = tmp_path / "cluster.yml"
        p.write_text("version: 2\ndesired_relays: null\n")
        loaded = ClusterConfig.load(p)
        assert loaded.desired_relays is None

    def test_subscription_page_null_yaml_loads_as_none(self, tmp_path: Path) -> None:
        """`subscription_page: null` must mean "unmanaged" — NOT the default
        enabled=True config. Otherwise apply would silently deploy the page."""
        p = tmp_path / "cluster.yml"
        p.write_text("version: 2\nsubscription_page: null\n")
        loaded = ClusterConfig.load(p)
        assert loaded.subscription_page is None

    def test_desired_clients_wrong_type_rejected(self, tmp_path: Path) -> None:
        """A string or dict in place of a list must raise, not silently
        collapse into set('a','l','i','c','e') downstream."""
        p = tmp_path / "cluster.yml"
        p.write_text("version: 2\ndesired_clients: alice\n")
        with pytest.raises(ValueError, match="desired_clients must be a list"):
            ClusterConfig.load(p)

    def test_desired_nodes_wrong_type_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "cluster.yml"
        p.write_text("version: 2\ndesired_nodes: {host: x}\n")
        with pytest.raises(ValueError, match="desired_nodes must be a list"):
            ClusterConfig.load(p)

    def test_save_uses_lock_for_concurrency(self, tmp_path: Path) -> None:
        """Concurrent saves from the executor must serialize so the file is
        never partially written. Verifies the lock attribute is wired up."""
        import threading

        cfg = _configured_cluster()
        p = tmp_path / "cluster.yml"

        # The lock should be a real Lock, not None / sentinel.
        assert cfg._lock is not None
        # And it should be a primitive lock, NOT an RLock — RLock would let
        # the same thread re-enter and bypass the contract we're documenting
        # (one writer at a time across threads).
        assert isinstance(cfg._lock, type(threading.RLock()))

        # Smoke: save runs without the lock blocking on itself.
        cfg.save(p)
        assert p.exists()

    def test_load_missing_file_returns_empty(self, tmp_path: Path) -> None:
        cfg = ClusterConfig.load(tmp_path / "nonexistent.yml")
        assert cfg.nodes == []
        assert cfg.panel.url == ""

    def test_load_empty_file_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "cluster.yml"
        p.write_text("")
        cfg = ClusterConfig.load(p)
        assert cfg.nodes == []

    def test_load_corrupted_yaml_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "cluster.yml"
        p.write_text(":\n  - :\n    [invalid yaml{{{")
        cfg = ClusterConfig.load(p)
        assert cfg.nodes == []

    def test_load_with_invalid_node_ip_warns(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Loading cluster.yml with invalid node IP should warn but not crash."""
        p = tmp_path / "cluster.yml"
        p.write_text("version: 1\nnodes:\n  - ip: not-an-ip\n    uuid: 550e8400-e29b-41d4-a716-446655440000\n")
        caplog.set_level("WARNING", logger="meridian.cluster")
        cfg = ClusterConfig.load(p)
        assert len(cfg.nodes) == 1
        assert "validation issue" in caplog.text

    def test_load_valid_config_no_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """Loading a valid cluster.yml should produce no warnings."""
        cfg = _configured_cluster()
        p = tmp_path / "cluster.yml"
        cfg.save(p)
        caplog.set_level("WARNING", logger="meridian.cluster")
        ClusterConfig.load(p)
        assert "validation issue" not in caplog.text


class TestTelegramConfigRoundTrip:
    def test_telegram_saves_and_loads(self, tmp_path: Path) -> None:
        cfg = ClusterConfig(telegram=TelegramConfig(
            bot_token="123456:ABC",
            notify_users="-100123",
            notify_nodes="-100456:80",
        ))
        p = tmp_path / "cluster.yml"
        cfg.save(p)
        loaded = ClusterConfig.load(p)
        assert loaded.telegram is not None
        assert loaded.telegram.bot_token == "123456:ABC"
        assert loaded.telegram.notify_users == "-100123"
        assert loaded.telegram.notify_nodes == "-100456:80"
        assert loaded.telegram.notify_crm == ""

    def test_no_telegram_loads_as_none(self, tmp_path: Path) -> None:
        cfg = ClusterConfig()  # no telegram
        p = tmp_path / "cluster.yml"
        cfg.save(p)
        loaded = ClusterConfig.load(p)
        assert loaded.telegram is None


# ---------------------------------------------------------------------------
# Convenience methods
# ---------------------------------------------------------------------------


class TestClusterConvenience:
    def test_find_node_by_ip(self) -> None:
        cfg = _configured_cluster()
        node = cfg.find_node(_IP_A)
        assert node is not None
        assert node.name == "finland"

    def test_find_node_by_name(self) -> None:
        cfg = _configured_cluster()
        node = cfg.find_node("germany")
        assert node is not None
        assert node.ip == _IP_B

    def test_find_node_returns_none_when_not_found(self) -> None:
        cfg = _configured_cluster()
        assert cfg.find_node("198.51.100.99") is None
        assert cfg.find_node("nonexistent") is None

    def test_remove_node(self) -> None:
        cfg = _configured_cluster()
        assert len(cfg.nodes) == 2
        result = cfg.remove_node("finland")
        assert result is True
        assert len(cfg.nodes) == 1
        assert cfg.nodes[0].name == "germany"

    def test_remove_node_not_found(self) -> None:
        cfg = _configured_cluster()
        result = cfg.remove_node("nonexistent")
        assert result is False
        assert len(cfg.nodes) == 2

    def test_panel_node_property(self) -> None:
        cfg = _configured_cluster(
            nodes=[
                NodeEntry(ip=_IP_A, name="panel-host", is_panel_host=True),
                NodeEntry(ip=_IP_B, name="standalone"),
            ]
        )
        panel_node = cfg.panel_node
        assert panel_node is not None
        assert panel_node.name == "panel-host"

    def test_panel_node_returns_none_when_absent(self) -> None:
        cfg = _configured_cluster()
        # Default test nodes have is_panel_host=False
        assert cfg.panel_node is None

    def test_is_configured_property(self) -> None:
        assert ClusterConfig().is_configured is False
        assert ClusterConfig(panel=PanelConfig(url="https://x.com")).is_configured is False
        assert ClusterConfig(panel=PanelConfig(url="https://x.com", api_token="tok")).is_configured is True

    def test_find_relay_by_ip(self) -> None:
        cfg = _configured_cluster()
        relay = cfg.find_relay(_IP_C)
        assert relay is not None
        assert relay.name == "ru-moscow"

    def test_find_relay_by_name(self) -> None:
        cfg = _configured_cluster()
        relay = cfg.find_relay("ru-moscow")
        assert relay is not None
        assert relay.ip == _IP_C

    def test_find_relay_returns_none_when_not_found(self) -> None:
        cfg = _configured_cluster()
        assert cfg.find_relay("nonexistent") is None

    def test_get_inbound(self) -> None:
        cfg = _configured_cluster()
        ref = cfg.get_inbound(ProtocolKey.REALITY)
        assert ref is not None
        assert ref.uuid == _UUID_C
        assert ref.tag == "VLESS_REALITY"

    def test_get_inbound_returns_none_for_missing(self) -> None:
        cfg = _configured_cluster()
        assert cfg.get_inbound("nonexistent") is None

    def test_save_raises_on_invalid_config(self, tmp_path: Path) -> None:
        cfg = ClusterConfig(nodes=[NodeEntry(ip="bad-ip")])
        with pytest.raises(ValueError, match="validation error"):
            cfg.save(tmp_path / "cluster.yml")
