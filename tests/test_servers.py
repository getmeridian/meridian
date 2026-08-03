"""Tests for server registry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from meridian.cluster import ClusterConfig, NodeEntry, ResourceAllocation, WorkloadBinding
from meridian.commands.server import run_add, run_list, run_remove
from meridian.console import set_json_mode, set_quiet_mode
from meridian.core.errors import LocalStateCorruptedError, LocalStateError
from meridian.core.servers import ServerConnectionDraft, profile_from_draft
from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    ExitIntent,
    ProtocolPathIntent,
    SetupIntent,
)
from meridian.servers import SERVER_REGISTRY_SCHEMA, ServerEntry, ServerProfileStore, ServerRegistry


class TestServerEntry:
    def test_fields(self) -> None:
        entry = ServerEntry("198.51.100.10", "ubuntu", "edge", port=2222)

        assert entry.host == "198.51.100.10"
        assert entry.ssh_user == "ubuntu"
        assert entry.title == "edge"
        assert entry.port == 2222


class TestServerRegistry:
    def test_empty_registry(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        assert reg.list() == []
        assert reg.count() == 0
        assert reg.find("anything") is None

    def test_add_and_list(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "root", "server1"))
        reg.add(ServerEntry("198.51.100.11", "ubuntu", "server2"))

        entries = reg.list()
        assert len(entries) == 2
        assert entries[0].host == "198.51.100.10"
        assert entries[1].host == "198.51.100.11"

    def test_add_deduplicates_by_host(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "root", "old-name"))
        reg.add(ServerEntry("198.51.100.10", "ubuntu", "new-name"))

        entries = reg.list()
        assert len(entries) == 1
        assert entries[0].user == "ubuntu"
        assert entries[0].name == "new-name"

    def test_add_rejects_name_collision_on_different_host(self, servers_file: Path) -> None:
        registry = ServerRegistry(servers_file)
        registry.add(ServerEntry("198.51.100.10", "root", "edge"))
        original = registry.find("edge")

        with pytest.raises(LocalStateError, match="identity conflicts"):
            registry.add(ServerEntry("198.51.100.20", "root", "edge"))

        assert registry.find("edge") == original
        assert registry.find("198.51.100.20") is None

    def test_add_rejects_host_that_matches_another_profile_title(self, servers_file: Path) -> None:
        registry = ServerRegistry(servers_file)
        registry.add(ServerEntry("198.51.100.10", "root", "198.51.100.20"))

        with pytest.raises(LocalStateError, match="identity conflicts"):
            registry.add(ServerEntry("198.51.100.20", "root"))

        assert registry.count() == 1
        assert registry.find("198.51.100.10") is not None

    def test_add_persists_custom_ssh_port(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "ubuntu", "edge", port=2222))

        # Data persists in servers.json
        assert reg.find("edge").port == 2222

    def test_find_by_ip(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "root", "myserver"))

        found = reg.find("198.51.100.10")
        assert found is not None
        assert found.name == "myserver"

    def test_find_by_name(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "root", "myserver"))

        found = reg.find("myserver")
        assert found is not None
        assert found.host == "198.51.100.10"

    def test_find_not_found(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "root", "myserver"))
        assert reg.find("nonexistent") is None

    def test_list_reads_json_profiles(self, servers_file: Path) -> None:
        profile = profile_from_draft(
            ServerConnectionDraft(title="Family VPN", host="198.51.100.10", ssh_user="ubuntu", ssh_port=2222)
        )
        ServerProfileStore(servers_file.with_suffix(".json")).upsert(profile)
        reg = ServerRegistry(servers_file)

        entry = reg.find("Family VPN")

        assert entry is not None
        assert entry.host == "198.51.100.10"
        assert entry.user == "ubuntu"
        assert entry.port == 2222

    def test_find_reads_profile_ids(self, servers_file: Path) -> None:
        profile = profile_from_draft(
            ServerConnectionDraft(title="Family VPN", host="198.51.100.10", ssh_user="ubuntu", ssh_port=2222)
        ).model_copy(update={"key_path": "/tmp/meridian_ed25519"})
        ServerProfileStore(servers_file.with_suffix(".json")).upsert(profile)

        entry = ServerRegistry(servers_file).find(profile.id)

        assert entry is not None
        assert entry.host == "198.51.100.10"
        assert entry.user == "ubuntu"
        assert entry.name == "Family VPN"
        assert entry.port == 2222
        assert entry.key_path == "/tmp/meridian_ed25519"

    def test_registry_add_preserves_v2_profile_key_path(self, servers_file: Path) -> None:
        profile = profile_from_draft(
            ServerConnectionDraft(title="Family VPN", host="198.51.100.10", ssh_user="ubuntu", ssh_port=2222)
        ).model_copy(update={"auth_state": "key_ready", "key_path": "/tmp/meridian_ed25519"})
        store = ServerProfileStore(servers_file)
        store.upsert(profile)

        ServerRegistry(servers_file).add(ServerEntry("198.51.100.10", "ubuntu", "Family VPN", port=2222))

        saved = store.find(profile.id)
        assert saved is not None
        assert saved.key_path == "/tmp/meridian_ed25519"
        assert saved.auth_state == "key_ready"

    def test_registry_update_preserves_immutable_profile_id(self, servers_file: Path) -> None:
        registry = ServerRegistry(servers_file)
        registry.add(ServerEntry("198.51.100.10", "root", "edge"))
        original = registry.find("edge")
        assert original is not None

        registry.add(
            ServerEntry(
                "198.51.100.11",
                "ubuntu",
                "renamed-edge",
                port=2222,
                id=original.id,
            )
        )

        updated = registry.find(original.id)
        assert updated is not None
        assert updated.id == original.id
        assert updated.host == "198.51.100.11"
        assert updated.name == "renamed-edge"
        assert registry.count() == 1

    def test_registry_remove_rejects_deployed_server(self, servers_file: Path) -> None:
        registry = ServerRegistry(servers_file)
        registry.add(ServerEntry("198.51.100.10", "root", "edge"))
        cluster = ClusterConfig(nodes=[NodeEntry(ip="198.51.100.10", name="edge")])

        with pytest.raises(LocalStateError, match="still used by node"):
            registry.remove("edge", cluster=cluster)

        assert registry.find("edge") is not None

    def test_registry_remove_rejects_v4_topology_reference(self, servers_file: Path) -> None:
        registry = ServerRegistry(servers_file)
        registry.add(ServerEntry("198.51.100.10", "root", "control"))
        entry = registry.find("control")
        assert entry is not None
        cluster = ClusterConfig(
            topology_intent=SetupIntent(
                control=ControlPlaneIntent(server_ref=entry.id),
                exits=[
                    ExitIntent(
                        id="exit-a",
                        server_ref="srv-exit",
                        paths=[
                            ProtocolPathIntent(
                                id="reality-a",
                                protocol="reality",
                                reality_sni="www.example.com",
                            )
                        ],
                    )
                ],
                default_egress_ref="exit-a",
                access=AccessIntent(users=["alice"]),
            )
        )

        with pytest.raises(LocalStateError, match="V4 control plane"):
            registry.remove(entry.id, cluster=cluster)

        assert registry.find(entry.id) is not None

    def test_registry_remove_ignores_inactive_v4_generation_state(self, servers_file: Path) -> None:
        registry = ServerRegistry(servers_file)
        registry.add(ServerEntry("198.51.100.10", "root", "retired-edge"))
        entry = registry.find("retired-edge")
        assert entry is not None
        cluster = ClusterConfig(
            workloads=[
                WorkloadBinding(
                    id="exit-a",
                    generation=1,
                    active=False,
                    server_refs=[entry.id],
                    inbound_uuids={"inbound:exit-a:reality": "inbound-uuid"},
                )
            ],
            allocations={
                "inbound:exit-a:reality": ResourceAllocation(
                    logical_id="inbound:exit-a:reality",
                    server_ref=entry.id,
                    transport="tcp",
                    port=10443,
                )
            },
        )

        assert registry.remove(entry.id, cluster=cluster) is True
        assert registry.find(entry.id) is None

    def test_remove_by_ip(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "root", "s1"))
        reg.add(ServerEntry("198.51.100.11", "root", "s2"))

        assert reg.remove("198.51.100.10") is True
        assert reg.count() == 1
        assert reg.find("198.51.100.10") is None

    def test_remove_by_name(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "root", "s1"))

        assert reg.remove("s1") is True
        assert reg.count() == 0

    def test_remove_not_found(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        assert reg.remove("nonexistent") is False


def test_server_add_invalid_input_prints_readable_validation_error(capsys: pytest.CaptureFixture[str]) -> None:
    set_json_mode(False)
    set_quiet_mode(False)

    with (
        patch("meridian.commands.server.ServerConnection") as mock_connection,
        pytest.raises(typer.Exit) as exc_info,
    ):
        run_add("not-an-ip")

    captured = capsys.readouterr()
    assert exc_info.value.exit_code == 2
    assert "Invalid server add request" in captured.err
    assert "ip: Enter a valid IP address." in captured.err
    assert "ValidationError" not in captured.err
    assert "pydantic" not in captured.err.lower()
    mock_connection.assert_not_called()


def test_server_add_invalid_port_prints_readable_validation_error(capsys: pytest.CaptureFixture[str]) -> None:
    set_json_mode(False)
    set_quiet_mode(False)

    with (
        patch("meridian.commands.server.ServerConnection") as mock_connection,
        pytest.raises(typer.Exit) as exc_info,
    ):
        run_add("198.51.100.10", ssh_port=70000)

    captured = capsys.readouterr()
    assert exc_info.value.exit_code == 2
    assert "Invalid server add request" in captured.err
    assert "ssh_port:" in captured.err
    assert "ValidationError" not in captured.err
    mock_connection.assert_not_called()


def test_server_add_persists_custom_port(servers_file: Path) -> None:
    with (
        patch("meridian.commands.server.SERVER_PROFILES_FILE", servers_file),
        patch("meridian.commands.server.ServerConnection") as mock_connection,
    ):
        run_add("198.51.100.10", name="edge-a", user="ubuntu", ssh_port=2222)

    mock_connection.assert_called_once_with(ip="198.51.100.10", user="ubuntu", local_mode=False, port=2222)
    entry = ServerRegistry(servers_file).find("edge-a")
    assert entry is not None
    assert entry.port == 2222
    assert entry.auth_state == "validated"
    assert entry.last_validated_at


def test_server_add_rejects_name_retarget_before_ssh(servers_file: Path) -> None:
    registry = ServerRegistry(servers_file)
    registry.add(ServerEntry("198.51.100.10", "root", "edge"))

    with (
        patch("meridian.commands.server.SERVER_PROFILES_FILE", servers_file),
        patch("meridian.commands.server.ServerConnection") as connection,
        pytest.raises(typer.Exit) as exc_info,
    ):
        run_add("198.51.100.20", name="edge")

    assert exc_info.value.exit_code == 2
    connection.assert_not_called()
    assert registry.find("edge").host == "198.51.100.10"


def test_server_add_reports_registry_save_failure_after_ssh(servers_file: Path) -> None:
    with (
        patch("meridian.commands.server.SERVER_PROFILES_FILE", servers_file),
        patch("meridian.commands.server.ServerConnection"),
        patch("meridian.commands.server.ServerRegistry.add", side_effect=OSError("disk full")),
        pytest.raises(typer.Exit) as exc_info,
    ):
        run_add("198.51.100.10", name="edge")

    assert exc_info.value.exit_code == 3


def test_server_list_reports_malformed_registry(servers_file: Path) -> None:
    with (
        patch("meridian.commands.server.SERVER_PROFILES_FILE", servers_file),
        patch(
            "meridian.commands.server.ServerRegistry.list",
            side_effect=LocalStateCorruptedError("Malformed servers.json"),
        ),
        pytest.raises(typer.Exit) as exc_info,
    ):
        run_list()

    assert exc_info.value.exit_code == 2


def test_server_remove_requires_confirmation(servers_file: Path) -> None:
    registry = ServerRegistry(servers_file)
    registry.add(ServerEntry("198.51.100.10", "root", "edge"))

    with (
        patch("meridian.commands.server.SERVER_PROFILES_FILE", servers_file),
        patch("meridian.commands.server.confirm", return_value=False),
        pytest.raises(typer.Exit) as exc_info,
    ):
        run_remove("edge")

    assert exc_info.value.exit_code == 1
    assert registry.find("edge") is not None


def test_server_remove_yes_skips_confirmation(servers_file: Path) -> None:
    registry = ServerRegistry(servers_file)
    registry.add(ServerEntry("198.51.100.10", "root", "edge"))

    with (
        patch("meridian.commands.server.SERVER_PROFILES_FILE", servers_file),
        patch("meridian.commands.server.confirm") as mock_confirm,
        patch("meridian.commands.server.ClusterConfig.load", return_value=ClusterConfig()),
    ):
        run_remove("edge", yes=True)

    mock_confirm.assert_not_called()
    assert registry.find("edge") is None


class TestServerProfileStore:
    def test_new_profiles_get_unique_connection_independent_ids(self) -> None:
        draft = ServerConnectionDraft(title="Edge", host="198.51.100.10")

        first = profile_from_draft(draft)
        second = profile_from_draft(draft)

        assert first.id.startswith("srv-")
        assert second.id.startswith("srv-")
        assert first.id != second.id

    def test_upsert_find_and_remove_profiles_by_human_refs(self, tmp_path: Path) -> None:
        store = ServerProfileStore(tmp_path / "servers.json")
        profile = profile_from_draft(
            ServerConnectionDraft(title="Family VPN", host="198.51.100.10", ssh_user="ubuntu", ssh_port=2222)
        )

        store.upsert(profile)

        assert store.find(profile.id) == profile
        assert store.find("Family VPN") == profile
        assert store.find("198.51.100.10") == profile
        assert store.remove("Family VPN") is True
        assert store.list() == []

    def test_json_store_uses_current_schema(self, tmp_path: Path) -> None:
        path = tmp_path / "servers.json"
        store = ServerProfileStore(path)
        store.upsert(profile_from_draft(ServerConnectionDraft(title="Edge", host="198.51.100.10")))

        assert f'"schema": "{SERVER_REGISTRY_SCHEMA}"' in path.read_text()
        assert path.stat().st_mode & 0o777 == 0o600

    def test_empty_json_store_requires_recovery(self, tmp_path: Path) -> None:
        path = tmp_path / "servers.json"
        path.write_text("")

        with pytest.raises(LocalStateCorruptedError, match="file is empty"):
            ServerProfileStore(path).list()

    def test_malformed_json_store_requires_recovery(self, tmp_path: Path) -> None:
        path = tmp_path / "servers.json"
        path.write_text("{broken")

        with pytest.raises(LocalStateCorruptedError, match="JSON is malformed"):
            ServerProfileStore(path).list()

    def test_invalid_profile_is_not_silently_dropped(self, tmp_path: Path) -> None:
        path = tmp_path / "servers.json"
        path.write_text(
            '{"schema":"meridian.servers/v3","servers":[{"id":"srv-one","title":"Edge","host":"not-an-ip"}]}'
        )

        with pytest.raises(LocalStateCorruptedError, match=r"servers\[0\] is invalid"):
            ServerProfileStore(path).list()

    def test_duplicate_profile_identity_requires_recovery(self, tmp_path: Path) -> None:
        path = tmp_path / "servers.json"
        profile = profile_from_draft(ServerConnectionDraft(title="Edge", host="198.51.100.10"))
        duplicate = profile.model_copy(update={"host": "198.51.100.11"})
        path.write_text(
            f'{{"schema":"meridian.servers/v3","servers":[{profile.model_dump_json()},{duplicate.model_dump_json()}]}}'
        )

        with pytest.raises(LocalStateCorruptedError, match="duplicate server id"):
            ServerProfileStore(path).list()
