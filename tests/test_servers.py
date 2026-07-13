"""Tests for server registry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from meridian.commands.server import run_add
from meridian.console import set_json_mode, set_quiet_mode
from meridian.core.servers import ServerConnectionDraft, profile_from_draft
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


class TestServerProfileStore:
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

    def test_json_store_uses_v2_schema(self, tmp_path: Path) -> None:
        path = tmp_path / "servers.json"
        store = ServerProfileStore(path)
        store.upsert(profile_from_draft(ServerConnectionDraft(title="Edge", host="198.51.100.10")))

        assert f'"schema": "{SERVER_REGISTRY_SCHEMA}"' in path.read_text()
        assert path.stat().st_mode & 0o777 == 0o600
