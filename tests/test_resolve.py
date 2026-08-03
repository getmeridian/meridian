"""Tests for server resolution logic — all 5+ resolution paths."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from meridian.cluster import ClusterConfig, NodeEntry, RelayEntry
from meridian.commands.resolve import resolve_server, try_resolve_server
from meridian.core.servers import ServerConnectionDraft, profile_from_draft
from meridian.resolve import is_local_keyword
from meridian.servers import ServerEntry, ServerProfileStore, ServerRegistry


def test_try_resolve_returns_none_for_cli_exit(servers_file: Path) -> None:
    assert try_resolve_server(ServerRegistry(servers_file)) is None


class TestExplicitIP:
    """Path 1: explicit_ip argument takes highest priority."""

    def test_explicit_ip_returns_resolved(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg, explicit_ip="198.51.100.10")
        assert result.ip == "198.51.100.10"
        assert result.user == "root"  # default
        assert result.local_mode is False

    def test_explicit_ip_with_user(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg, explicit_ip="198.51.100.10", user="ubuntu")
        assert result.user == "ubuntu"

    def test_explicit_ip_picks_user_from_registry(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "ubuntu", "mybox"))
        result = resolve_server(reg, explicit_ip="198.51.100.10")
        assert result.user == "ubuntu"  # resolved from registry

    def test_explicit_user_overrides_registry(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "ubuntu", "mybox"))
        result = resolve_server(reg, explicit_ip="198.51.100.10", user="admin")
        assert result.user == "admin"  # explicit overrides


class TestServerFlag:
    """Path 2: --server flag (by name or IP)."""

    def test_server_by_name(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "root", "mybox"))
        result = resolve_server(reg, requested_server="mybox")
        assert result.ip == "198.51.100.10"
        assert result.user == "root"

    def test_server_by_ip(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "ubuntu", "mybox"))
        result = resolve_server(reg, requested_server="198.51.100.10")
        assert result.ip == "198.51.100.10"
        assert result.user == "ubuntu"

    def test_server_ip_not_in_registry(self, servers_file: Path) -> None:
        """Bare IP via --server should still resolve even if not registered."""
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg, requested_server="198.51.100.99")
        assert result.ip == "198.51.100.99"
        assert result.user == "root"

    def test_server_name_not_found_exits(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        with pytest.raises(typer.Exit) as exc_info:
            resolve_server(reg, requested_server="nonexistent")
        assert exc_info.value.exit_code != 0

    def test_server_by_name_inherits_user(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "deploy", "prod"))
        result = resolve_server(reg, requested_server="prod")
        assert result.user == "deploy"

    def test_server_by_profile_uses_saved_key_path(self, servers_file: Path) -> None:
        profile = profile_from_draft(
            ServerConnectionDraft(title="Family VPN", host="198.51.100.10", ssh_user="ubuntu", ssh_port=2222)
        ).model_copy(update={"key_path": "/tmp/meridian_ed25519"})
        ServerProfileStore(servers_file.with_suffix(".json")).upsert(profile)
        reg = ServerRegistry(servers_file)

        result = resolve_server(reg, requested_server=profile.id)

        assert result.ip == "198.51.100.10"
        assert result.user == "ubuntu"
        assert result.conn.identity_file == "/tmp/meridian_ed25519"
        assert result.conn.multiplex is False

    def test_explicit_user_override_does_not_use_saved_key_path(self, servers_file: Path) -> None:
        profile = profile_from_draft(
            ServerConnectionDraft(title="Family VPN", host="198.51.100.10", ssh_user="ubuntu", ssh_port=2222)
        ).model_copy(update={"key_path": "/tmp/meridian_ed25519"})
        ServerProfileStore(servers_file.with_suffix(".json")).upsert(profile)
        reg = ServerRegistry(servers_file)

        result = resolve_server(reg, requested_server=profile.id, user="admin")

        assert result.user == "admin"
        assert result.conn.identity_file == ""


class TestSingleServerAutoSelect:
    """Path 4: single registered server auto-selected."""

    def test_single_server_auto_select(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("meridian.resolve.detect_local_server_ip", lambda: None)
        monkeypatch.setattr("meridian.cluster.ClusterConfig.load", lambda: ClusterConfig())
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "root", "only-one"))
        result = resolve_server(reg)
        assert result.ip == "198.51.100.10"
        assert result.user == "root"

    def test_auto_select_ignores_cluster_relay_only_entries(
        self, servers_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("meridian.resolve.detect_local_server_ip", lambda: None)
        cluster = ClusterConfig(
            nodes=[NodeEntry(ip="198.51.100.10")],
            relays=[
                RelayEntry(ip="203.0.113.10", exit_node_ip="198.51.100.10"),
                RelayEntry(ip="203.0.113.11", exit_node_ip="198.51.100.10"),
            ],
        )
        monkeypatch.setattr("meridian.cluster.ClusterConfig.load", lambda: cluster)
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "root", "exit"))
        reg.add(ServerEntry("203.0.113.10", "root", "relay-a"))
        reg.add(ServerEntry("203.0.113.11", "root", "relay-b"))

        result = resolve_server(reg)
        assert result.ip == "198.51.100.10"
        assert result.user == "root"

    def test_dual_role_server_remains_auto_selectable(
        self, servers_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("meridian.resolve.detect_local_server_ip", lambda: None)
        cluster = ClusterConfig(
            nodes=[NodeEntry(ip="198.51.100.10")],
            relays=[RelayEntry(ip="198.51.100.10", exit_node_ip="198.51.100.10")],
        )
        monkeypatch.setattr("meridian.cluster.ClusterConfig.load", lambda: cluster)
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "root", "exit-relay"))

        result = resolve_server(reg)
        assert result.ip == "198.51.100.10"
        assert result.user == "root"


class TestMultipleServers:
    """Path 5: multiple servers registered, no selection."""

    def test_multiple_servers_fail(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("meridian.resolve.detect_local_server_ip", lambda: None)
        monkeypatch.setattr("meridian.cluster.ClusterConfig.load", lambda: ClusterConfig())
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "root", "server1"))
        reg.add(ServerEntry("198.51.100.11", "root", "server2"))
        with pytest.raises(typer.Exit) as exc_info:
            resolve_server(reg)
        assert exc_info.value.exit_code != 0

    def test_multiple_real_exits_do_not_auto_select_with_cluster_relay(
        self, servers_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("meridian.resolve.detect_local_server_ip", lambda: None)
        cluster = ClusterConfig(
            nodes=[NodeEntry(ip="198.51.100.10"), NodeEntry(ip="198.51.100.11")],
            relays=[RelayEntry(ip="203.0.113.10", exit_node_ip="198.51.100.10")],
        )
        monkeypatch.setattr("meridian.cluster.ClusterConfig.load", lambda: cluster)
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "root", "exit-a"))
        reg.add(ServerEntry("198.51.100.11", "root", "exit-b"))
        reg.add(ServerEntry("203.0.113.10", "root", "relay-a"))

        with pytest.raises(typer.Exit) as exc_info:
            resolve_server(reg)
        assert exc_info.value.exit_code != 0


class TestNoServers:
    """Path 6: empty registry, no explicit IP."""

    def test_no_servers_fail(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("meridian.resolve.detect_local_server_ip", lambda: None)
        monkeypatch.setattr("meridian.cluster.ClusterConfig.load", lambda: ClusterConfig())
        reg = ServerRegistry(servers_file)
        with pytest.raises(typer.Exit) as exc_info:
            resolve_server(reg)
        assert exc_info.value.exit_code != 0


class TestLocalMode:
    """Path 3: running on the server itself as root."""

    def test_local_mode_detection(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("meridian.resolve.detect_local_server_ip", lambda: "198.51.100.10")
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg)
        assert result.ip == "198.51.100.10"
        assert result.local_mode is True


class TestLocalKeyword:
    """'local'/'locally' keyword triggers on-server deployment."""

    def test_is_local_keyword(self) -> None:
        assert is_local_keyword("local")
        assert is_local_keyword("Local")
        assert is_local_keyword("LOCAL")
        assert is_local_keyword("locally")
        assert is_local_keyword("Locally")
        assert not is_local_keyword("localhost")
        assert not is_local_keyword("198.51.100.10")

    def test_explicit_ip_local_keyword(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "meridian.resolve.detect_public_ip",
            lambda: "203.0.113.10",
        )
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg, explicit_ip="local")
        assert result.ip == "203.0.113.10"
        assert result.local_mode is True

    def test_explicit_ip_locally_keyword(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "meridian.resolve.detect_public_ip",
            lambda: "203.0.113.10",
        )
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg, explicit_ip="locally")
        assert result.ip == "203.0.113.10"
        assert result.local_mode is True

    def test_server_flag_local_keyword(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "meridian.resolve.detect_public_ip",
            lambda: "203.0.113.20",
        )
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg, requested_server="local")
        assert result.ip == "203.0.113.20"
        assert result.local_mode is True

    def test_local_keyword_fails_without_public_ip(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "meridian.resolve.detect_public_ip",
            lambda: "",
        )
        reg = ServerRegistry(servers_file)
        with pytest.raises(typer.Exit) as exc_info:
            resolve_server(reg, explicit_ip="local")
        assert exc_info.value.exit_code != 0

    def test_local_keyword_conn_has_local_mode(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "meridian.resolve.detect_public_ip",
            lambda: "203.0.113.30",
        )
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg, explicit_ip="local")
        assert result.conn.local_mode is True
        assert result.conn.ip == "203.0.113.30"


class TestResolvedServer:
    """Test ResolvedServer dataclass properties."""

    def test_frozen(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg, explicit_ip="198.51.100.10")
        with pytest.raises(AttributeError):
            result.ip = "changed"  # type: ignore[misc]

    def test_conn_created(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg, explicit_ip="198.51.100.10", user="ubuntu")
        assert result.conn.ip == "198.51.100.10"
        assert result.conn.user == "ubuntu"
        assert result.conn.local_mode is False
