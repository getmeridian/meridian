"""Tests for operations.py — reusable provisioning operations.

Uses MagicMock for panel API and cluster config mutations.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from meridian.cluster import ClusterConfig, NodeEntry, PanelConfig, RelayEntry
from meridian.operations import (
    add_client,
    load_applied_snapshot,
    remove_client,
    remove_node,
    stop_node_containers,
    update_node,
)
from meridian.remnawave import MeridianPanel


def _make_cluster(**kwargs) -> ClusterConfig:
    defaults = {
        "version": 2,
        "panel": PanelConfig(url="https://198.51.100.1/panel", api_token="tok", server_ip="198.51.100.1"),
    }
    defaults.update(kwargs)
    return ClusterConfig(**defaults)


def _mock_panel() -> MagicMock:
    panel = MagicMock(spec=MeridianPanel)
    return panel


@pytest.fixture(autouse=True)
def _successful_node_cleanup() -> Iterator[None]:
    """Keep operation tests focused unless they explicitly exercise cleanup."""
    with (
        patch("meridian.operations.stop_node_containers", return_value=True),
        patch.object(ClusterConfig, "backup"),
    ):
        yield


# ---------------------------------------------------------------------------
# Client operations
# ---------------------------------------------------------------------------


class TestAddClient:
    def test_passes_squad_uuids(self) -> None:
        cluster = _make_cluster(squad_uuid="sq-1")
        panel = _mock_panel()
        panel.create_user.return_value = SimpleNamespace(uuid="u-1", username="alice")
        add_client(cluster, panel, name="alice")
        panel.create_user.assert_called_once_with("alice", squad_uuids=["sq-1"])

    def test_no_squad_when_empty(self) -> None:
        cluster = _make_cluster(squad_uuid="")
        panel = _mock_panel()
        panel.create_user.return_value = SimpleNamespace(uuid="u-1", username="alice")
        add_client(cluster, panel, name="alice")
        panel.create_user.assert_called_once_with("alice", squad_uuids=None)


class TestRemoveClient:
    def test_looks_up_by_username_then_deletes(self) -> None:
        cluster = _make_cluster()
        panel = _mock_panel()
        panel.get_user.return_value = SimpleNamespace(uuid="u-1", username="alice")
        panel.delete_user.return_value = True
        result = remove_client(cluster, panel, name="alice")
        panel.get_user.assert_called_once_with("alice")
        panel.delete_user.assert_called_once_with("u-1")
        assert result is True

    def test_returns_false_when_user_not_found(self) -> None:
        cluster = _make_cluster()
        panel = _mock_panel()
        panel.get_user.return_value = None
        result = remove_client(cluster, panel, name="ghost")
        assert result is False


# ---------------------------------------------------------------------------
# Node operations
# ---------------------------------------------------------------------------


class TestRemoveNode:
    def test_container_cleanup_uses_deployed_path_and_allows_absent_compose(self) -> None:
        node = NodeEntry(ip="198.51.100.2", ssh_user="ubuntu", ssh_port=2222)
        connection = MagicMock()
        connection.run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")

        assert stop_node_containers(node, connection=connection) is True

        command = connection.run.call_args.args[0]
        assert "/opt/remnanode/docker-compose.yml" in command
        assert "/opt/remnawave-node" not in command
        assert "if [ ! -f" in command

    def test_removes_cluster_tracked_node(self) -> None:
        node = NodeEntry(ip="198.51.100.2", uuid="uuid-2", name="node-2")
        cluster = _make_cluster(nodes=[node])
        panel = _mock_panel()
        remove_node(cluster, panel, node_ip="198.51.100.2")
        panel.disable_node.assert_called_once_with("uuid-2")
        panel.delete_node.assert_called_once_with("uuid-2")
        assert len(cluster.nodes) == 0

    def test_rejects_panel_host(self) -> None:
        node = NodeEntry(ip="198.51.100.1", uuid="uuid-1", is_panel_host=True)
        cluster = _make_cluster(nodes=[node])
        panel = _mock_panel()
        with pytest.raises(ValueError, match="panel node"):
            remove_node(cluster, panel, node_ip="198.51.100.1")

    def test_rejects_node_with_dependent_relays(self) -> None:
        node = NodeEntry(ip="198.51.100.2", uuid="uuid-2")
        relay = RelayEntry(ip="198.51.100.10", exit_node_ip="198.51.100.2")
        cluster = _make_cluster(nodes=[node], relays=[relay])
        panel = _mock_panel()
        with pytest.raises(ValueError, match="relays depend"):
            remove_node(cluster, panel, node_ip="198.51.100.2")

    def test_panel_only_node_lookup_by_address(self) -> None:
        """Node not in cluster.yml but exists in panel API."""
        cluster = _make_cluster(nodes=[])
        panel = _mock_panel()
        panel.find_node_by_address.return_value = SimpleNamespace(uuid="api-uuid")
        remove_node(cluster, panel, node_ip="198.51.100.5")
        panel.find_node_by_address.assert_called_once_with("198.51.100.5")
        panel.disable_node.assert_called_once_with("api-uuid")
        panel.delete_node.assert_called_once_with("api-uuid")


class TestUpdateNode:
    def test_rollback_on_failed_redeploy(self) -> None:
        node = NodeEntry(ip="198.51.100.2", uuid="uuid-2", name="old", sni="old.sni", domain="old.dom", warp=False)
        cluster = _make_cluster(nodes=[node])
        panel = _mock_panel()

        with (
            patch("meridian.panel_bootstrap.setup_redeploy", side_effect=RuntimeError("SSH failed")),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.resolve.ResolvedServer"),
        ):
            with pytest.raises(RuntimeError, match="SSH failed"):
                update_node(cluster, panel, ip="198.51.100.2", name="new", sni="new.sni")

        # Metadata should be rolled back
        assert node.name == "old"
        assert node.sni == "old.sni"

    def test_name_update_calls_panel_api(self) -> None:
        node = NodeEntry(ip="198.51.100.2", uuid="uuid-2", name="old-name")
        cluster = _make_cluster(nodes=[node])
        panel = _mock_panel()

        with (
            patch("meridian.panel_bootstrap.setup_redeploy"),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.resolve.ResolvedServer"),
        ):
            update_node(cluster, panel, ip="198.51.100.2", name="new-name")

        assert node.name == "new-name"
        panel.update_node_name.assert_called_once_with("uuid-2", "new-name")


# ---------------------------------------------------------------------------
# Error/failure paths surfaced by the Codex test-quality review
# ---------------------------------------------------------------------------


class TestRemoveNodeErrorPaths:
    """Panel deletion must succeed before local retry state is discarded."""

    def test_container_cleanup_failure_retains_node_and_panel_registration(self) -> None:
        node = NodeEntry(ip="198.51.100.2", uuid="uuid-2", name="x")
        cluster = _make_cluster(nodes=[node])
        panel = _mock_panel()

        with (
            patch("meridian.operations.stop_node_containers", return_value=False),
            pytest.raises(RuntimeError, match="Could not stop containers"),
        ):
            remove_node(cluster, panel, node_ip=node.ip)

        assert cluster.nodes == [node]
        panel.disable_node.assert_not_called()
        panel.delete_node.assert_not_called()

    def test_panel_disable_failure_does_not_block_successful_delete(self) -> None:
        from meridian.remnawave import RemnawaveError

        node = NodeEntry(ip="198.51.100.2", uuid="uuid-2", name="x")
        cluster = _make_cluster(nodes=[node])
        panel = _mock_panel()
        panel.disable_node.side_effect = RemnawaveError("panel down")

        remove_node(cluster, panel, node_ip="198.51.100.2")
        assert cluster.nodes == []

    def test_panel_delete_failure_retains_node_for_retry(self) -> None:
        from meridian.remnawave import RemnawaveError

        node = NodeEntry(ip="198.51.100.2", uuid="uuid-2", name="x")
        cluster = _make_cluster(nodes=[node])
        panel = _mock_panel()
        panel.delete_node.side_effect = RemnawaveError("panel down")

        with pytest.raises(RemnawaveError, match="panel down"):
            remove_node(cluster, panel, node_ip="198.51.100.2")

        assert cluster.nodes == [node]

    def test_force_removes_dependent_relays_before_node(self) -> None:
        node = NodeEntry(ip="198.51.100.2", uuid="uuid-2")
        relay = RelayEntry(ip="198.51.100.10", exit_node_ip="198.51.100.2")
        cluster = _make_cluster(nodes=[node], relays=[relay])
        panel = _mock_panel()

        def remove_dependent(target_cluster: ClusterConfig, _panel: MagicMock, *, relay_ip: str) -> None:
            target_cluster.relays = [item for item in target_cluster.relays if item.ip != relay_ip]

        with patch("meridian.operations.remove_relay", side_effect=remove_dependent) as remove_relay:
            remove_node(cluster, panel, node_ip="198.51.100.2", force=True)

        remove_relay.assert_called_once_with(cluster, panel, relay_ip=relay.ip)
        assert cluster.nodes == []
        assert cluster.relays == []


class TestUpdateNodeMetadataConsistency:
    """update_node mutates the node metadata BEFORE the redeploy runs so that
    _setup_redeploy reads the new SNI/name. If redeploy fails, the rollback
    must restore EVERY field that was tentatively changed — otherwise
    cluster.yml claims state that the server never reached.
    """

    def test_rollback_restores_all_changed_fields(self) -> None:
        node = NodeEntry(
            ip="198.51.100.2",
            uuid="uuid-2",
            name="old-name",
            sni="old.sni",
            domain="old.dom",
            warp=False,
        )
        cluster = _make_cluster(nodes=[node])
        panel = _mock_panel()

        with (
            patch("meridian.panel_bootstrap.setup_redeploy", side_effect=RuntimeError("provisioner died")),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.resolve.ResolvedServer"),
        ):
            with pytest.raises(RuntimeError):
                update_node(
                    cluster,
                    panel,
                    ip="198.51.100.2",
                    name="new-name",
                    sni="new.sni",
                    domain="new.dom",
                    warp=True,
                )

        # Every changed field must be the original value.
        assert node.name == "old-name"
        assert node.sni == "old.sni"
        assert node.domain == "old.dom"
        assert node.warp is False
        # Panel update should NOT have been called when redeploy never finished.
        panel.update_node_name.assert_not_called()

    def test_panel_name_update_failure_does_not_undo_local_change(self) -> None:
        from meridian.remnawave import RemnawaveError

        node = NodeEntry(ip="198.51.100.2", uuid="uuid-2", name="old-name")
        cluster = _make_cluster(nodes=[node])
        panel = _mock_panel()
        panel.update_node_name.side_effect = RemnawaveError("panel down")

        with (
            patch("meridian.panel_bootstrap.setup_redeploy"),  # provisioner OK
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.resolve.ResolvedServer"),
        ):
            update_node(cluster, panel, ip="198.51.100.2", name="new-name")

        # The redeploy succeeded so the local rename should stick even if the
        # panel name sync fails — the panel name is cosmetic and we recover
        # next apply.
        assert node.name == "new-name"


class TestAddClientErrorPaths:
    def test_panel_create_failure_propagates(self) -> None:
        from meridian.remnawave import RemnawaveError

        cluster = _make_cluster(squad_uuid="sq-1")
        panel = _mock_panel()
        panel.create_user.side_effect = RemnawaveError("user already exists")

        with pytest.raises(RemnawaveError, match="already exists"):
            add_client(cluster, panel, name="alice")


# ---------------------------------------------------------------------------
# Hybrid imperative ↔ declarative sync (discussion getmeridian/meridian#27)
# ---------------------------------------------------------------------------
#
# Imperative commands must mirror their effect into the matching desired_*
# list when (and only when) the user has opted into declarative for that
# resource type. desired_* == None means "unmanaged" — leaving it alone
# preserves backwards-compatible behaviour. desired_* == [] or a populated
# list means "managed" — failing to mirror would cause the next
# `meridian apply` to interpret the freshly-added resource as drift and
# remove it.


class TestHybridDesiredClientsSync:
    def test_add_client_unmanaged_leaves_desired_none(self) -> None:
        cluster = _make_cluster(squad_uuid="sq-1", desired_clients=None)
        panel = _mock_panel()
        panel.create_user.return_value = SimpleNamespace(uuid="u-1", username="alice")

        with patch.object(ClusterConfig, "save") as save:
            add_client(cluster, panel, name="alice")

        assert cluster.desired_clients is None
        save.assert_not_called()

    def test_add_client_managed_appends_and_saves(self) -> None:
        cluster = _make_cluster(squad_uuid="sq-1", desired_clients=["existing"])
        panel = _mock_panel()
        panel.create_user.return_value = SimpleNamespace(uuid="u-1", username="alice")

        with patch.object(ClusterConfig, "save") as save:
            add_client(cluster, panel, name="alice")

        assert cluster.desired_clients == ["existing", "alice"]
        save.assert_called_once()

    def test_add_client_managed_idempotent_no_double_append(self) -> None:
        cluster = _make_cluster(desired_clients=["alice"])
        panel = _mock_panel()
        panel.create_user.return_value = SimpleNamespace(uuid="u-1", username="alice")

        with patch.object(ClusterConfig, "save") as save:
            add_client(cluster, panel, name="alice")

        assert cluster.desired_clients == ["alice"]
        save.assert_not_called()

    def test_remove_client_managed_drops_and_saves(self) -> None:
        cluster = _make_cluster(desired_clients=["alice", "bob"])
        panel = _mock_panel()
        panel.get_user.return_value = SimpleNamespace(uuid="u-1", username="alice")
        panel.delete_user.return_value = True

        with patch.object(ClusterConfig, "save") as save:
            remove_client(cluster, panel, name="alice")

        assert cluster.desired_clients == ["bob"]
        save.assert_called_once()

    def test_remove_client_unmanaged_leaves_desired_none(self) -> None:
        cluster = _make_cluster(desired_clients=None)
        panel = _mock_panel()
        panel.get_user.return_value = SimpleNamespace(uuid="u-1", username="alice")
        panel.delete_user.return_value = True

        with patch.object(ClusterConfig, "save") as save:
            remove_client(cluster, panel, name="alice")

        assert cluster.desired_clients is None
        save.assert_not_called()


class TestHybridDesiredNodesSync:
    def test_remove_node_managed_drops_and_saves(self) -> None:
        from meridian.cluster import DesiredNode

        node = NodeEntry(ip="198.51.100.5", uuid="u-5", name="extra")
        cluster = _make_cluster(
            nodes=[node],
            desired_nodes=[DesiredNode(host="198.51.100.5", name="extra")],
        )
        panel = _mock_panel()

        with patch.object(ClusterConfig, "save"):
            remove_node(cluster, panel, node_ip="198.51.100.5")

        assert cluster.nodes == []
        assert cluster.desired_nodes == []

    def test_remove_node_unmanaged_leaves_desired_none(self) -> None:
        node = NodeEntry(ip="198.51.100.5", uuid="u-5", name="extra")
        cluster = _make_cluster(nodes=[node], desired_nodes=None)
        panel = _mock_panel()

        with patch.object(ClusterConfig, "save"):
            remove_node(cluster, panel, node_ip="198.51.100.5")

        assert cluster.desired_nodes is None

    def test_update_node_managed_mirrors_metadata(self) -> None:
        from meridian.cluster import DesiredNode

        node = NodeEntry(ip="198.51.100.6", uuid="u-6", name="old", sni="old.sni")
        cluster = _make_cluster(
            nodes=[node],
            desired_nodes=[DesiredNode(host="198.51.100.6", name="old", sni="old.sni")],
        )
        panel = _mock_panel()

        with (
            patch("meridian.panel_bootstrap.setup_redeploy"),
            patch("meridian.ssh.ServerConnection"),
            patch("meridian.resolve.ResolvedServer"),
            patch.object(ClusterConfig, "save"),
        ):
            update_node(cluster, panel, ip="198.51.100.6", name="new", sni="new.sni")

        assert cluster.desired_nodes is not None
        assert cluster.desired_nodes[0].name == "new"
        assert cluster.desired_nodes[0].sni == "new.sni"


class TestHybridDesiredRelaysSync:
    def test_remove_relay_service_failure_retains_local_state(self) -> None:
        relay = RelayEntry(ip="198.51.100.20", exit_node_ip="198.51.100.1", name="r1")
        cluster = _make_cluster(relays=[relay])
        panel = _mock_panel()

        with (
            patch("meridian.relay_ops.stop_relay_service", return_value=False),
            patch("meridian.relay_ops.delete_relay_hosts") as delete_hosts,
            patch("meridian.ssh.ServerConnection"),
            pytest.raises(RuntimeError, match="Could not stop relay service"),
        ):
            from meridian.operations import remove_relay

            remove_relay(cluster, panel, relay_ip=relay.ip)

        assert cluster.relays == [relay]
        delete_hosts.assert_not_called()

    def test_remove_relay_nginx_failure_retains_local_state(self) -> None:
        exit_node = NodeEntry(ip="198.51.100.1", sni="exit.example")
        relay = RelayEntry(
            ip="198.51.100.20",
            exit_node_ip=exit_node.ip,
            name="r1",
            sni="relay.example",
        )
        cluster = _make_cluster(nodes=[exit_node], relays=[relay])
        panel = _mock_panel()

        with (
            patch("meridian.relay_ops.stop_relay_service", return_value=True),
            patch("meridian.relay_ops.remove_relay_artifacts", return_value=True),
            patch("meridian.relay_ops.remove_relay_nginx", return_value=False),
            patch("meridian.relay_ops.delete_relay_hosts") as delete_hosts,
            patch("meridian.ssh.ServerConnection"),
            pytest.raises(RuntimeError, match="Could not clean up nginx"),
        ):
            from meridian.operations import remove_relay

            remove_relay(cluster, panel, relay_ip=relay.ip)

        assert cluster.relays == [relay]
        delete_hosts.assert_not_called()

    def test_remove_relay_panel_failure_retains_local_state(self) -> None:
        relay = RelayEntry(ip="198.51.100.20", exit_node_ip="198.51.100.1", name="r1")
        cluster = _make_cluster(relays=[relay])
        panel = _mock_panel()

        with (
            patch("meridian.relay_ops.stop_relay_service", return_value=True),
            patch("meridian.relay_ops.remove_relay_artifacts", return_value=True),
            patch("meridian.relay_ops.delete_relay_hosts", return_value=False),
            patch("meridian.ssh.ServerConnection"),
            pytest.raises(RuntimeError, match="Could not delete all panel hosts"),
        ):
            from meridian.operations import remove_relay

            remove_relay(cluster, panel, relay_ip=relay.ip)

        assert cluster.relays == [relay]

    def test_remove_relay_managed_drops_and_saves(self) -> None:
        from meridian.cluster import DesiredRelay

        relay = RelayEntry(ip="198.51.100.20", exit_node_ip="198.51.100.1", name="r1")
        cluster = _make_cluster(
            relays=[relay],
            desired_relays=[DesiredRelay(host="198.51.100.20", name="r1", exit_node="exit-1")],
        )
        panel = _mock_panel()

        with (
            patch("meridian.relay_ops.delete_relay_hosts", return_value=True),
            patch("meridian.relay_ops.remove_relay_nginx", return_value=True),
            patch("meridian.relay_ops.stop_relay_service", return_value=True),
            patch("meridian.relay_ops.remove_relay_artifacts", return_value=True),
            patch("meridian.ssh.ServerConnection"),
            patch.object(ClusterConfig, "save"),
        ):
            from meridian.operations import remove_relay

            remove_relay(cluster, panel, relay_ip="198.51.100.20")

        assert cluster.relays == []
        assert cluster.desired_relays == []

    def test_remove_relay_unmanaged_leaves_desired_none(self) -> None:
        relay = RelayEntry(ip="198.51.100.20", exit_node_ip="198.51.100.1", name="r1")
        cluster = _make_cluster(relays=[relay], desired_relays=None)
        panel = _mock_panel()

        with (
            patch("meridian.relay_ops.delete_relay_hosts", return_value=True),
            patch("meridian.relay_ops.remove_relay_nginx", return_value=True),
            patch("meridian.relay_ops.stop_relay_service", return_value=True),
            patch("meridian.relay_ops.remove_relay_artifacts", return_value=True),
            patch("meridian.ssh.ServerConnection"),
            patch.object(ClusterConfig, "save"),
        ):
            from meridian.operations import remove_relay

            remove_relay(cluster, panel, relay_ip="198.51.100.20")

        assert cluster.desired_relays is None


# ---------------------------------------------------------------------------
# Hybrid-sync applied-snapshot mirroring (Critical #2 regression guard)
# ---------------------------------------------------------------------------
#
# Without snapshot mirroring, the sequence:
#   1. `meridian client add bob`      (imperative; appends to desired_clients)
#   2. edit cluster.yml → remove bob  (user intent: deliberate removal)
#   3. `meridian apply --yes`         (expected: remove bob)
# produces DRIFT classification for bob (bob was never in the last applied
# snapshot), so --yes silently skips the removal. The fix: imperative
# add/remove must also mirror into cluster.applied_state, so
# compute_plan classifies the later removal as intentional (from_extras=False).


class TestHybridSyncAppliedSnapshot:
    def test_client_add_mirrors_into_applied_snapshot(self) -> None:
        cluster = _make_cluster(desired_clients=["default"])
        panel = _mock_panel()
        panel.create_user.return_value = SimpleNamespace(uuid="u-1", username="bob")
        with patch.object(ClusterConfig, "save"):
            add_client(cluster, panel, name="bob")
        assert cluster.applied_state.clients == ["bob"]

    def test_client_remove_mirrors_into_applied_snapshot(self) -> None:
        cluster = _make_cluster(desired_clients=["default", "bob"])
        cluster.applied_state.clients = ["default", "bob"]
        panel = _mock_panel()
        panel.get_user.return_value = SimpleNamespace(uuid="u-bob", username="bob")
        panel.delete_user.return_value = True
        with patch.object(ClusterConfig, "save"):
            remove_client(cluster, panel, name="bob")
        assert cluster.applied_state.clients == ["default"]

    def test_client_add_noop_when_desired_is_none(self) -> None:
        """Unmanaged clients: applied snapshot must NOT appear from nowhere."""
        cluster = _make_cluster(desired_clients=None)
        panel = _mock_panel()
        panel.create_user.return_value = SimpleNamespace(uuid="u-1", username="bob")
        with patch.object(ClusterConfig, "save"):
            add_client(cluster, panel, name="bob")
        assert cluster.applied_state.clients is None

    def test_client_add_tolerates_malformed_snapshot(self) -> None:
        """If applied_state has junk where applied snapshot should be, mirror resets it."""
        cluster = _make_cluster(desired_clients=["default"])
        cluster.applied_state.clients = "corrupt-string"  # type: ignore[assignment]  # wrong type
        panel = _mock_panel()
        panel.create_user.return_value = SimpleNamespace(uuid="u-1", username="bob")
        with patch.object(ClusterConfig, "save"):
            add_client(cluster, panel, name="bob")
        assert cluster.applied_state.clients == ["bob"]

    def test_load_snapshot_rejects_string(self) -> None:
        """A bare string must NOT explode into a set of chars (Codex finding #3)."""
        cluster = _make_cluster()
        cluster.applied_state.clients = "alice"  # type: ignore[assignment]
        assert load_applied_snapshot(cluster, "clients") is None

    def test_load_snapshot_rejects_dict(self) -> None:
        cluster = _make_cluster()
        cluster.applied_state.clients = {"not": "a list"}  # type: ignore[assignment]
        assert load_applied_snapshot(cluster, "clients") is None

    def test_load_snapshot_filters_non_string_items(self) -> None:
        """Garbage entries are skipped; clean entries still load."""
        cluster = _make_cluster()
        cluster.applied_state.clients = ["alice", 42, None, "bob"]  # type: ignore[list-item]
        result = load_applied_snapshot(cluster, "clients")
        assert result == {"alice", "bob"}

    def test_load_snapshot_empty_list_returns_empty_set(self) -> None:
        """Empty snapshot returns empty set -- 'managed, converged to zero'.
        This is semantically distinct from None ('no history')."""
        cluster = _make_cluster()
        cluster.applied_state.clients = []
        result = load_applied_snapshot(cluster, "clients")
        assert result == set()

    def test_load_snapshot_none_returns_none(self) -> None:
        """None snapshot means 'no history' -- conservative drift classification."""
        cluster = _make_cluster()
        cluster.applied_state.clients = None
        assert load_applied_snapshot(cluster, "clients") is None

    def test_load_snapshot_returns_set_when_populated(self) -> None:
        cluster = _make_cluster()
        cluster.applied_state.clients = ["alice", "bob"]
        assert load_applied_snapshot(cluster, "clients") == {"alice", "bob"}

    def test_relay_remove_mirrors_into_applied_snapshot(self) -> None:
        from meridian.cluster import DesiredRelay

        relay = RelayEntry(ip="198.51.100.20", exit_node_ip="198.51.100.1", name="r1")
        desired = [DesiredRelay(host="198.51.100.20", name="r1", exit_node="198.51.100.1")]
        cluster = _make_cluster(relays=[relay], desired_relays=desired)
        cluster.applied_state.relays = ["198.51.100.20"]
        panel = _mock_panel()
        with (
            patch("meridian.relay_ops.delete_relay_hosts", return_value=True),
            patch("meridian.relay_ops.remove_relay_nginx", return_value=True),
            patch("meridian.relay_ops.stop_relay_service", return_value=True),
            patch("meridian.relay_ops.remove_relay_artifacts", return_value=True),
            patch("meridian.ssh.ServerConnection"),
            patch.object(ClusterConfig, "save"),
        ):
            from meridian.operations import remove_relay

            remove_relay(cluster, panel, relay_ip="198.51.100.20")
        assert cluster.applied_state.relays == []
