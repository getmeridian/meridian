"""Tests for server resolution logic — all 5+ resolution paths."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from meridian.commands.resolve import (
    _check_version_mismatch,
    _warned_servers,
    fetch_credentials,
    is_local_keyword,
    resolve_server,
)
from meridian.config import SERVER_CREDS_DIR
from meridian.core.servers import ServerConnectionDraft, profile_from_draft
from meridian.servers import SERVER_ROLE_RELAY, ServerEntry, ServerProfileStore, ServerRegistry


class TestExplicitIP:
    """Path 1: explicit_ip argument takes highest priority."""

    def test_explicit_ip_returns_resolved(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg, explicit_ip="1.2.3.4")
        assert result.ip == "1.2.3.4"
        assert result.user == "root"  # default
        assert result.local_mode is False

    def test_explicit_ip_with_user(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg, explicit_ip="1.2.3.4", user="ubuntu")
        assert result.user == "ubuntu"

    def test_explicit_ip_picks_user_from_registry(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("1.2.3.4", "ubuntu", "mybox"))
        result = resolve_server(reg, explicit_ip="1.2.3.4")
        assert result.user == "ubuntu"  # resolved from registry

    def test_explicit_user_overrides_registry(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("1.2.3.4", "ubuntu", "mybox"))
        result = resolve_server(reg, explicit_ip="1.2.3.4", user="admin")
        assert result.user == "admin"  # explicit overrides

    def test_explicit_ip_creds_dir(self, tmp_home: Path, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg, explicit_ip="5.6.7.8")
        assert result.creds_dir == tmp_home / "credentials" / "5.6.7.8"


class TestServerFlag:
    """Path 2: --server flag (by name or IP)."""

    def test_server_by_name(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("1.2.3.4", "root", "mybox"))
        result = resolve_server(reg, requested_server="mybox")
        assert result.ip == "1.2.3.4"
        assert result.user == "root"

    def test_server_by_ip(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("1.2.3.4", "ubuntu", "mybox"))
        result = resolve_server(reg, requested_server="1.2.3.4")
        assert result.ip == "1.2.3.4"
        assert result.user == "ubuntu"

    def test_server_ip_not_in_registry(self, servers_file: Path) -> None:
        """Bare IP via --server should still resolve even if not registered."""
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg, requested_server="9.8.7.6")
        assert result.ip == "9.8.7.6"
        assert result.user == "root"

    def test_server_name_not_found_exits(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        with pytest.raises(typer.Exit) as exc_info:
            resolve_server(reg, requested_server="nonexistent")
        assert exc_info.value.exit_code != 0

    def test_server_by_name_inherits_user(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("1.2.3.4", "deploy", "prod"))
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
        # Patch _detect_local_mode_from_creds to return None (not on server)
        monkeypatch.setattr(
            "meridian.commands.resolve.detect_local_mode_from_creds",
            lambda: None,
        )
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("10.20.30.40", "root", "only-one"))
        result = resolve_server(reg)
        assert result.ip == "10.20.30.40"
        assert result.user == "root"

    def test_auto_select_ignores_relay_only_registry_entries(
        self, tmp_home: Path, servers_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "meridian.commands.resolve.detect_local_mode_from_creds",
            lambda: None,
        )
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("10.20.30.40", "root", "exit"))
        reg.add(ServerEntry("203.0.113.10", "root", "relay-a"))
        reg.add(ServerEntry("203.0.113.11", "root", "relay-b"))

        creds_dir = tmp_home / "credentials" / "10.20.30.40"
        creds_dir.mkdir(parents=True)
        (creds_dir / "proxy.yml").write_text("version: 2\nserver:\n  ip: 10.20.30.40\n")
        for relay_ip in ("203.0.113.10", "203.0.113.11"):
            relay_dir = tmp_home / "credentials" / relay_ip
            relay_dir.mkdir(parents=True)
            (relay_dir / "relay.yml").write_text("role: relay\n")

        result = resolve_server(reg)
        assert result.ip == "10.20.30.40"
        assert result.user == "root"

    def test_auto_select_uses_role_tagged_exit_on_fresh_machine(
        self, servers_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "meridian.commands.resolve.detect_local_mode_from_creds",
            lambda: None,
        )
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("10.20.30.40", "root", "exit"))
        reg.add(ServerEntry("203.0.113.10", "root", "relay-a", SERVER_ROLE_RELAY))
        reg.add(ServerEntry("203.0.113.11", "root", "relay-b", SERVER_ROLE_RELAY))

        result = resolve_server(reg)
        assert result.ip == "10.20.30.40"
        assert result.user == "root"


class TestMultipleServers:
    """Path 5: multiple servers registered, no selection."""

    def test_multiple_servers_fail(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "meridian.commands.resolve.detect_local_mode_from_creds",
            lambda: None,
        )
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("1.2.3.4", "root", "server1"))
        reg.add(ServerEntry("5.6.7.8", "root", "server2"))
        with pytest.raises(typer.Exit) as exc_info:
            resolve_server(reg)
        assert exc_info.value.exit_code != 0

    def test_multiple_real_exits_do_not_auto_select_when_only_one_is_cached(
        self, tmp_home: Path, servers_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "meridian.commands.resolve.detect_local_mode_from_creds",
            lambda: None,
        )
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "root", "exit-a"))
        reg.add(ServerEntry("198.51.100.11", "root", "exit-b"))
        reg.add(ServerEntry("203.0.113.10", "root", "relay-a", SERVER_ROLE_RELAY))

        cached = tmp_home / "credentials" / "198.51.100.10"
        cached.mkdir(parents=True)
        (cached / "proxy.yml").write_text("version: 2\nserver:\n  ip: 198.51.100.10\n")

        with pytest.raises(typer.Exit) as exc_info:
            resolve_server(reg)
        assert exc_info.value.exit_code != 0


class TestNoServers:
    """Path 6: empty registry, no explicit IP."""

    def test_no_servers_fail(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "meridian.commands.resolve.detect_local_mode_from_creds",
            lambda: None,
        )
        reg = ServerRegistry(servers_file)
        with pytest.raises(typer.Exit) as exc_info:
            resolve_server(reg)
        assert exc_info.value.exit_code != 0


class TestLocalMode:
    """Path 3: running on the server itself as root."""

    def test_local_mode_detection(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "meridian.commands.resolve.detect_local_mode_from_creds",
            lambda: "10.0.0.1",
        )
        monkeypatch.setattr("meridian.config.os.geteuid", lambda: 0)
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg)
        assert result.ip == "10.0.0.1"
        assert result.local_mode is True
        assert result.creds_dir == SERVER_CREDS_DIR

    def test_local_mode_non_root_uses_user_creds_dir(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "meridian.commands.resolve.detect_local_mode_from_creds",
            lambda: "10.0.0.1",
        )
        monkeypatch.setattr("meridian.config.os.geteuid", lambda: 1000)
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg)
        assert result.ip == "10.0.0.1"
        assert result.local_mode is True
        assert result.creds_dir != SERVER_CREDS_DIR


class TestLocalKeyword:
    """'local'/'locally' keyword triggers on-server deployment."""

    def test_is_local_keyword(self) -> None:
        assert is_local_keyword("local")
        assert is_local_keyword("Local")
        assert is_local_keyword("LOCAL")
        assert is_local_keyword("locally")
        assert is_local_keyword("Locally")
        assert not is_local_keyword("localhost")
        assert not is_local_keyword("1.2.3.4")

    def test_explicit_ip_local_keyword(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "meridian.commands.resolve.detect_public_ip",
            lambda: "203.0.113.10",
        )
        monkeypatch.setattr("meridian.config.os.geteuid", lambda: 0)
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg, explicit_ip="local")
        assert result.ip == "203.0.113.10"
        assert result.local_mode is True
        assert result.creds_dir == SERVER_CREDS_DIR

    def test_explicit_ip_locally_keyword(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "meridian.commands.resolve.detect_public_ip",
            lambda: "203.0.113.10",
        )
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg, explicit_ip="locally")
        assert result.ip == "203.0.113.10"
        assert result.local_mode is True

    def test_server_flag_local_keyword(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "meridian.commands.resolve.detect_public_ip",
            lambda: "203.0.113.20",
        )
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg, requested_server="local")
        assert result.ip == "203.0.113.20"
        assert result.local_mode is True

    def test_local_keyword_fails_without_public_ip(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "meridian.commands.resolve.detect_public_ip",
            lambda: "",
        )
        reg = ServerRegistry(servers_file)
        with pytest.raises(typer.Exit) as exc_info:
            resolve_server(reg, explicit_ip="local")
        assert exc_info.value.exit_code != 0

    def test_local_keyword_conn_has_local_mode(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "meridian.commands.resolve.detect_public_ip",
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
        result = resolve_server(reg, explicit_ip="1.2.3.4")
        with pytest.raises(AttributeError):
            result.ip = "changed"  # type: ignore[misc]

    def test_conn_created(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg, explicit_ip="1.2.3.4", user="ubuntu")
        assert result.conn.ip == "1.2.3.4"
        assert result.conn.user == "ubuntu"
        assert result.conn.local_mode is False


class TestVersionMismatchCheck:
    """Version mismatch warning logic."""

    @pytest.fixture(autouse=True)
    def _clear_warned(self) -> None:
        """Reset the warned servers set before each test."""
        _warned_servers.clear()

    def test_no_warning_when_versions_match(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        proxy = tmp_path / "proxy.yml"
        proxy.write_text("version: 2\nserver:\n  deployed_with: '3.5.0'\n")
        with patch("meridian.__version__", "3.5.2"):
            _check_version_mismatch("1.2.3.4", proxy)
        captured = capsys.readouterr()
        assert "mismatch" not in captured.err.lower()

    def test_warning_on_minor_mismatch(self, tmp_path: Path) -> None:
        proxy = tmp_path / "proxy.yml"
        proxy.write_text("version: 2\nserver:\n  deployed_with: '3.5.0'\n")
        with patch("meridian.__version__", "3.6.0"):
            _check_version_mismatch("1.2.3.4", proxy)
        assert "1.2.3.4" in _warned_servers

    def test_warning_on_major_mismatch(self, tmp_path: Path) -> None:
        proxy = tmp_path / "proxy.yml"
        proxy.write_text("version: 2\nserver:\n  deployed_with: '3.5.0'\n")
        with patch("meridian.__version__", "4.0.0"):
            _check_version_mismatch("1.2.3.4", proxy)
        assert "1.2.3.4" in _warned_servers

    def test_no_warning_when_deployed_with_empty(self, tmp_path: Path) -> None:
        proxy = tmp_path / "proxy.yml"
        proxy.write_text("version: 2\nserver:\n  ip: 1.2.3.4\n")
        with patch("meridian.__version__", "4.0.0"):
            _check_version_mismatch("1.2.3.4", proxy)
        assert "1.2.3.4" not in _warned_servers

    def test_warns_only_once_per_server(self, tmp_path: Path) -> None:
        proxy = tmp_path / "proxy.yml"
        proxy.write_text("version: 2\nserver:\n  deployed_with: '3.5.0'\n")
        with patch("meridian.__version__", "4.0.0"):
            _check_version_mismatch("1.2.3.4", proxy)
            assert "1.2.3.4" in _warned_servers
            # Second call should be a no-op (already warned)
            _check_version_mismatch("1.2.3.4", proxy)
        # Still just one entry
        assert len(_warned_servers) == 1

    def test_no_warning_on_patch_diff(self, tmp_path: Path) -> None:
        proxy = tmp_path / "proxy.yml"
        proxy.write_text("version: 2\nserver:\n  deployed_with: '3.5.0'\n")
        with patch("meridian.__version__", "3.5.7"):
            _check_version_mismatch("1.2.3.4", proxy)
        assert "1.2.3.4" not in _warned_servers


class TestFetchCredentials:
    """fetch_credentials edge cases."""

    def test_permission_error_on_mkdir_returns_false(self, servers_file: Path) -> None:
        """Non-root on server: mkdir on /etc/meridian raises PermissionError."""
        reg = ServerRegistry(servers_file)
        resolved = resolve_server(reg, explicit_ip="198.51.100.1")

        with patch.object(type(resolved.creds_dir), "mkdir", side_effect=PermissionError):
            result = fetch_credentials(resolved, force=True)

        assert result is False

    def test_force_refresh_ignores_cached_proxy(self, servers_file: Path, tmp_path: Path) -> None:
        reg = ServerRegistry(servers_file)
        resolved = resolve_server(reg, explicit_ip="198.51.100.1")
        cached = resolved.creds_dir / "proxy.yml"
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text("version: 2\nserver:\n  ip: 198.51.100.1\n")

        with patch.object(resolved.conn, "fetch_credentials", return_value=True) as mock_fetch:
            result = fetch_credentials(resolved, force=True)

        assert result is True
        mock_fetch.assert_called_once_with(resolved.creds_dir)
