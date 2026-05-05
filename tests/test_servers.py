"""Tests for server registry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from meridian.commands.server import run_add
from meridian.console import set_json_mode, set_quiet_mode
from meridian.servers import SERVER_ROLE_RELAY, ServerEntry, ServerRegistry


class TestServerEntry:
    def test_from_line_basic(self) -> None:
        entry = ServerEntry.from_line("1.2.3.4 root myserver")
        assert entry is not None
        assert entry.host == "1.2.3.4"
        assert entry.user == "root"
        assert entry.name == "myserver"

    def test_from_line_no_name(self) -> None:
        entry = ServerEntry.from_line("1.2.3.4 ubuntu")
        assert entry is not None
        assert entry.host == "1.2.3.4"
        assert entry.user == "ubuntu"
        assert entry.name == ""

    def test_from_line_with_role(self) -> None:
        entry = ServerEntry.from_line("1.2.3.4 root relay-a relay")
        assert entry is not None
        assert entry.host == "1.2.3.4"
        assert entry.user == "root"
        assert entry.name == "relay-a"
        assert entry.role == SERVER_ROLE_RELAY

    def test_from_line_with_role_and_empty_name(self) -> None:
        entry = ServerEntry.from_line("1.2.3.4 root - relay")
        assert entry is not None
        assert entry.name == ""
        assert entry.role == SERVER_ROLE_RELAY

    def test_from_line_comment(self) -> None:
        assert ServerEntry.from_line("# comment") is None

    def test_from_line_blank(self) -> None:
        assert ServerEntry.from_line("") is None
        assert ServerEntry.from_line("   ") is None

    def test_from_line_single_word(self) -> None:
        assert ServerEntry.from_line("onlyone") is None

    def test_str(self) -> None:
        assert str(ServerEntry("1.2.3.4", "root", "test")) == "1.2.3.4 root test"
        assert str(ServerEntry("1.2.3.4", "root")) == "1.2.3.4 root"
        assert str(ServerEntry("1.2.3.4", "root", "relay-a", SERVER_ROLE_RELAY)) == "1.2.3.4 root relay-a relay"
        assert str(ServerEntry("1.2.3.4", "root", role=SERVER_ROLE_RELAY)) == "1.2.3.4 root - relay"


class TestServerRegistry:
    def test_empty_registry(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        assert reg.list() == []
        assert reg.count() == 0
        assert reg.find("anything") is None

    def test_add_and_list(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("1.2.3.4", "root", "server1"))
        reg.add(ServerEntry("5.6.7.8", "ubuntu", "server2"))

        entries = reg.list()
        assert len(entries) == 2
        assert entries[0].host == "1.2.3.4"
        assert entries[1].host == "5.6.7.8"

    def test_add_deduplicates_by_host(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("1.2.3.4", "root", "old-name"))
        reg.add(ServerEntry("1.2.3.4", "ubuntu", "new-name"))

        entries = reg.list()
        assert len(entries) == 1
        assert entries[0].user == "ubuntu"
        assert entries[0].name == "new-name"

    def test_find_by_ip(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("1.2.3.4", "root", "myserver"))

        found = reg.find("1.2.3.4")
        assert found is not None
        assert found.name == "myserver"

    def test_find_by_name(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("1.2.3.4", "root", "myserver"))

        found = reg.find("myserver")
        assert found is not None
        assert found.host == "1.2.3.4"

    def test_find_not_found(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("1.2.3.4", "root", "myserver"))
        assert reg.find("nonexistent") is None

    def test_remove_by_ip(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("1.2.3.4", "root", "s1"))
        reg.add(ServerEntry("5.6.7.8", "root", "s2"))

        assert reg.remove("1.2.3.4") is True
        assert reg.count() == 1
        assert reg.find("1.2.3.4") is None

    def test_remove_by_name(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("1.2.3.4", "root", "s1"))

        assert reg.remove("s1") is True
        assert reg.count() == 0

    def test_remove_not_found(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        assert reg.remove("nonexistent") is False

    def test_preserves_comments(self, servers_file: Path) -> None:
        servers_file.write_text("# My servers\n1.2.3.4 root old\n")
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("5.6.7.8", "root", "new"))

        raw = servers_file.read_text()
        assert "# My servers" in raw
        assert "5.6.7.8" in raw


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
