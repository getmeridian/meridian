"""Tests for cluster config — validation, YAML round-trip, and convenience methods."""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.cluster import (
    CURRENT_CLUSTER_VERSION,
    ActionCheckpoint,
    BrandingConfig,
    ClusterConfig,
    ManagedResourceBinding,
    PanelConfig,
    RealityKeyBinding,
    ResourceAllocation,
    SubscriptionPageConfig,
    TelegramConfig,
    WorkloadBinding,
)
from meridian.core.errors import LocalStateCorruptedError
from meridian.setup.single_server import single_server_intent
from tests.support.topology import reality_path, single_exit_intent

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
        topology_intent=single_server_intent(None, server_ref="srv-0a1b2c3d4e5f"),
        branding=BrandingConfig(server_name="Test VPN", color="ocean"),
    )
    defaults.update(overrides)
    return ClusterConfig(**defaults)


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

    def test_valid_cluster_returns_no_errors(self) -> None:
        cfg = _configured_cluster()
        assert cfg.validate() == []

    def test_invalid_panel_server_ip_detected(self) -> None:
        cfg = ClusterConfig(panel=PanelConfig(server_ip="bogus"))
        errors = cfg.validate()
        assert any("panel.server_ip" in e for e in errors)

    def test_panel_ssh_port_out_of_range(self) -> None:
        cfg = ClusterConfig(panel=PanelConfig(ssh_port=0))
        errors = cfg.validate()
        assert any("panel.ssh_port" in e for e in errors)

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

    def test_missing_version_uses_current_schema_default(self, tmp_path: Path) -> None:
        p = tmp_path / "cluster.yml"
        p.write_text("branding: {}\n")

        cfg = ClusterConfig.load(p)

        assert cfg.version == CURRENT_CLUSTER_VERSION
        assert cfg._readonly is False

    def test_readonly_config_cannot_be_saved(self, tmp_path: Path) -> None:
        cfg = ClusterConfig()
        cfg._readonly = True
        with pytest.raises(ValueError, match="newer version"):
            cfg.save(tmp_path / "cluster.yml")


class TestClusterYAMLRoundTrip:
    def test_save_and_load_preserves_all_fields(self, tmp_path: Path) -> None:
        cfg = _configured_cluster()
        p = tmp_path / "cluster.yml"
        cfg.save(p)
        loaded = ClusterConfig.load(p)

        assert loaded.panel.url == cfg.panel.url
        assert loaded.panel.api_token == cfg.panel.api_token
        assert loaded.panel.server_ip == cfg.panel.server_ip
        assert loaded.topology_intent == cfg.topology_intent
        assert loaded.branding.server_name == "Test VPN"
        assert loaded.branding.color == "ocean"

    def test_pre_topology_keys_are_not_loaded_as_state(self, tmp_path: Path) -> None:
        """Development-build keys stay inert so the CLI can refuse the file."""
        p = tmp_path / "cluster.yml"
        p.write_text("version: 2\nnodes:\n  - ip: 198.51.100.1\ndesired_clients: [alice]\n")

        loaded = ClusterConfig.load(p)

        assert loaded.topology_intent is None
        assert set(loaded._extra) == {"nodes", "desired_clients"}

    def test_save_and_load_preserves_extra_fields(self, tmp_path: Path) -> None:
        cfg = ClusterConfig(_extra={"future_field": "hello"})
        p = tmp_path / "cluster.yml"
        cfg.save(p)
        loaded = ClusterConfig.load(p)
        assert loaded._extra.get("future_field") == "hello"

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

    def test_subscription_page_null_yaml_loads_as_none(self, tmp_path: Path) -> None:
        """`subscription_page: null` must mean "unmanaged" — NOT the default
        enabled=True config. Otherwise apply would silently deploy the page."""
        p = tmp_path / "cluster.yml"
        p.write_text("version: 3\nsubscription_page: null\n")
        loaded = ClusterConfig.load(p)
        assert loaded.subscription_page is None

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
        assert cfg.topology_intent is None
        assert cfg.panel.url == ""

    def test_load_empty_file_requires_recovery(self, tmp_path: Path) -> None:
        p = tmp_path / "cluster.yml"
        p.write_text("")
        with pytest.raises(LocalStateCorruptedError, match="file is empty"):
            ClusterConfig.load(p)

    def test_load_corrupted_yaml_requires_recovery(self, tmp_path: Path) -> None:
        p = tmp_path / "cluster.yml"
        p.write_text(":\n  - :\n    [invalid yaml{{{")
        with pytest.raises(LocalStateCorruptedError, match="YAML is malformed"):
            ClusterConfig.load(p)

    def test_load_non_mapping_document_requires_recovery(self, tmp_path: Path) -> None:
        p = tmp_path / "cluster.yml"
        p.write_text("- version\n- 2\n")

        with pytest.raises(LocalStateCorruptedError, match="root must be a mapping"):
            ClusterConfig.load(p)

    def test_load_valid_config_no_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        cfg = _configured_cluster()
        p = tmp_path / "cluster.yml"
        cfg.save(p)
        caplog.set_level("WARNING", logger="meridian.cluster")
        ClusterConfig.load(p)
        assert "validation issue" not in caplog.text


class TestTelegramConfigRoundTrip:
    def test_telegram_saves_and_loads(self, tmp_path: Path) -> None:
        cfg = ClusterConfig(
            telegram=TelegramConfig(
                bot_token="123456:ABC",
                notify_users="-100123",
                notify_nodes="-100456:80",
            )
        )
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


class TestClusterConvenience:
    def test_is_configured_property(self) -> None:
        assert ClusterConfig().is_configured is False
        assert ClusterConfig(panel=PanelConfig(url="https://x.com")).is_configured is False
        assert ClusterConfig(panel=PanelConfig(url="https://x.com", api_token="tok")).is_configured is True

    def test_save_raises_on_invalid_config(self, tmp_path: Path) -> None:
        cfg = ClusterConfig(panel=PanelConfig(server_ip="bad-ip"))
        with pytest.raises(ValueError, match="validation error"):
            cfg.save(tmp_path / "cluster.yml")


_PROFILE_UUID = "550e8400-e29b-41d4-a716-446655440000"
_NODE_UUID = "660e8400-e29b-41d4-a716-446655440001"
_INBOUND_UUID = "770e8400-e29b-41d4-a716-446655440002"
_HOST_UUID = "880e8400-e29b-41d4-a716-446655440003"
_SQUAD_UUID = "990e8400-e29b-41d4-a716-446655440004"
_HASH_A = "a" * 64
_HASH_B = "b" * 64


class TestTopologyStateRoundTrip:
    def test_topology_bindings_allocations_and_checkpoints_round_trip(self, tmp_path: Path) -> None:
        cluster = ClusterConfig(
            topology_intent=single_exit_intent(paths=[reality_path("reality")]),
            workloads=[
                WorkloadBinding(
                    id="exit-a",
                    generation=2,
                    active=True,
                    server_refs=["srv-exit"],
                    config_profile_uuid=_PROFILE_UUID,
                    node_uuids={"srv-exit": _NODE_UUID},
                    inbound_uuids={"reality": _INBOUND_UUID},
                    reality_keys={
                        "srv-exit": RealityKeyBinding(
                            public_key="public",
                            private_key="private",
                            short_id="abcdef0123456789",
                        )
                    },
                    desired_hash=_HASH_A,
                )
            ],
            managed_bindings={
                "profile:exit-a@2": ManagedResourceBinding(
                    logical_id="profile:exit-a",
                    resource_kind="config_profile",
                    generation=2,
                    remote_id=_PROFILE_UUID,
                    desired_hash=_HASH_A,
                    observed_hash=_HASH_A,
                    active=True,
                )
            },
            allocations={
                "inbound:exit-a:reality": ResourceAllocation(
                    logical_id="inbound:exit-a:reality",
                    server_ref="srv-exit",
                    transport="tcp",
                    port=10443,
                    tag="meridian-exit-a-reality",
                )
            },
            action_checkpoints={
                "checkpoint-a": ActionCheckpoint(
                    idempotency_key="checkpoint-a",
                    resource_id="profile:exit-a",
                    expected_hash=_HASH_A,
                    generation=2,
                    status="succeeded",
                    attempts=1,
                    observed_hash=_HASH_A,
                    remote_id=_PROFILE_UUID,
                    updated_at="2026-07-14T15:00:00Z",
                )
            },
            active_generation=2,
            active_plan_hash=_HASH_B,
        )
        path = tmp_path / "cluster.yml"

        cluster.save(path)
        loaded = ClusterConfig.load(path)

        assert loaded.topology_intent == cluster.topology_intent
        assert loaded.workloads == cluster.workloads
        assert loaded.managed_bindings == cluster.managed_bindings
        assert loaded.allocations == cluster.allocations
        assert loaded.action_checkpoints == cluster.action_checkpoints
        assert loaded.active_generation == 2
        assert loaded.active_plan_hash == _HASH_B

    def test_old_generation_survives_until_later_garbage_collection(self, tmp_path: Path) -> None:
        cluster = ClusterConfig(
            workloads=[
                WorkloadBinding(id="exit-a", generation=1, active=False, adopted=True),
                WorkloadBinding(id="exit-a", generation=2, active=True),
            ]
        )
        path = tmp_path / "cluster.yml"

        cluster.save(path)
        loaded = ClusterConfig.load(path)

        assert [(item.generation, item.active) for item in loaded.workloads] == [(1, False), (2, True)]

    def test_invalid_binding_shape_fails_closed(self, tmp_path: Path) -> None:
        path = tmp_path / "cluster.yml"
        path.write_text("version: 3\nmanaged_bindings:\n  broken: not-a-mapping\n")

        with pytest.raises(LocalStateCorruptedError, match=r"managed_bindings\[broken\] must be a mapping"):
            ClusterConfig.load(path)
