"""Tests for server resolution logic — all 5+ resolution paths."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from meridian.cluster import ClusterConfig
from meridian.commands.resolve import resolve_server
from meridian.core.servers import ServerConnectionDraft, profile_from_draft
from meridian.resolve import TargetSpec, is_local_keyword, merge_target, parse_target
from meridian.servers import ServerEntry, ServerProfileStore, ServerRegistry
from meridian.setup.editor import add_exit_to_intent, add_relay_to_intent
from meridian.setup.single_server import single_server_intent


def _topology_cluster(reg: ServerRegistry, *, exits: list[str], relays: list[str]) -> ClusterConfig:
    """Build a saved topology whose roles reference the registry's server IDs."""

    def ref(host: str) -> str:
        entry = reg.find(host)
        assert entry is not None
        return entry.id

    intent = single_server_intent(None, server_ref=ref(exits[0]))
    for index, host in enumerate(exits[1:], start=2):
        intent = add_exit_to_intent(intent, server_ref=ref(host), title=f"exit-{index}", reality_sni="www.example.com")
    for index, host in enumerate(relays, start=1):
        intent = add_relay_to_intent(
            intent, server_ref=ref(host), title=f"relay-{index}", exit_ref="main", listen_port=8443
        )
    return ClusterConfig(topology_intent=intent)


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


class TestTargetSyntax:
    """One positional target parsed like ssh, merged with flags and saved profiles."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("198.51.100.10", TargetSpec(host="198.51.100.10")),
            ("ubuntu@198.51.100.10:2222", TargetSpec(host="198.51.100.10", user="ubuntu", port=2222)),
            ("[2001:db8::1]:2200", TargetSpec(host="2001:db8::1", port=2200)),
            ("2001:db8::1", TargetSpec(host="2001:db8::1")),
            ("admin@[2001:db8::1]", TargetSpec(host="2001:db8::1", user="admin")),
            ("mybox", TargetSpec(host="mybox")),
            ("local", TargetSpec(local=True)),
        ],
    )
    def test_parses_ssh_style_targets(self, text: str, expected: TargetSpec) -> None:
        assert parse_target(text) == expected

    @pytest.mark.parametrize("text", ["bad user@198.51.100.10", "198.51.100.10:70000", "@198.51.100.10"])
    def test_rejects_malformed_targets(self, text: str) -> None:
        with pytest.raises(ValueError):
            parse_target(text)

    def test_flags_fill_gaps_but_cannot_contradict_the_target(self) -> None:
        assert merge_target("198.51.100.10", user="ubuntu", port=2222) == TargetSpec(
            host="198.51.100.10", user="ubuntu", port=2222
        )
        with pytest.raises(ValueError, match="given twice"):
            merge_target("ubuntu@198.51.100.10", user="admin")

    def test_positional_saved_name_resolves_to_its_host_not_the_name(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "ubuntu", "mybox", port=2222))

        result = resolve_server(reg, explicit_ip="mybox")

        assert (result.ip, result.user, result.port) == ("198.51.100.10", "ubuntu", 2222)

    def test_inline_user_and_port_override_the_saved_profile(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "ubuntu", "mybox"))

        result = resolve_server(reg, explicit_ip="admin@198.51.100.10:2200")

        assert (result.user, result.port) == ("admin", 2200)

    def test_unknown_bare_name_fails_instead_of_dialing_it(self, servers_file: Path) -> None:
        with pytest.raises(typer.Exit):
            resolve_server(ServerRegistry(servers_file), explicit_ip="mybox")


class TestSavedTarget:
    """Path 2: a saved name or IP as the target."""

    def test_server_by_name(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "root", "mybox"))
        result = resolve_server(reg, explicit_ip="mybox")
        assert result.ip == "198.51.100.10"
        assert result.user == "root"

    def test_server_by_ip(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "ubuntu", "mybox"))
        result = resolve_server(reg, explicit_ip="198.51.100.10")
        assert result.ip == "198.51.100.10"
        assert result.user == "ubuntu"

    def test_server_ip_not_in_registry(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        result = resolve_server(reg, explicit_ip="198.51.100.99")
        assert result.ip == "198.51.100.99"
        assert result.user == "root"

    def test_server_name_not_found_exits(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        with pytest.raises(typer.Exit) as exc_info:
            resolve_server(reg, explicit_ip="nonexistent")
        assert exc_info.value.exit_code != 0

    def test_server_by_name_inherits_user(self, servers_file: Path) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "deploy", "prod"))
        result = resolve_server(reg, explicit_ip="prod")
        assert result.user == "deploy"

    def test_server_by_profile_uses_saved_key_path(self, servers_file: Path) -> None:
        profile = profile_from_draft(
            ServerConnectionDraft(title="Family VPN", host="198.51.100.10", ssh_user="ubuntu", ssh_port=2222)
        ).model_copy(update={"key_path": "/tmp/meridian_ed25519"})
        ServerProfileStore(servers_file.with_suffix(".json")).upsert(profile)
        reg = ServerRegistry(servers_file)

        result = resolve_server(reg, explicit_ip=profile.id)

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

        result = resolve_server(reg, explicit_ip=profile.id, user="admin")

        assert result.user == "admin"
        assert result.conn.identity_file == ""


class TestSingleServerAutoSelect:
    """Path 4: single registered server auto-selected."""

    def test_single_server_auto_select(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("meridian.cluster.ClusterConfig.load", lambda: ClusterConfig())
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "root", "only-one"))
        result = resolve_server(reg)
        assert result.ip == "198.51.100.10"
        assert result.user == "root"

    def test_auto_select_ignores_cluster_relay_only_entries(
        self, servers_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "root", "exit"))
        reg.add(ServerEntry("203.0.113.10", "root", "relay-a"))
        reg.add(ServerEntry("203.0.113.11", "root", "relay-b"))
        cluster = _topology_cluster(reg, exits=["198.51.100.10"], relays=["203.0.113.10", "203.0.113.11"])
        monkeypatch.setattr("meridian.cluster.ClusterConfig.load", lambda: cluster)

        result = resolve_server(reg)
        assert result.ip == "198.51.100.10"
        assert result.user == "root"

    def test_dual_role_server_remains_auto_selectable(
        self, servers_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.9", "root", "main-exit"))
        reg.add(ServerEntry("198.51.100.10", "root", "exit-relay"))
        # 198.51.100.10 is an exit of its own and a relay hop in front of main.
        cluster = _topology_cluster(reg, exits=["198.51.100.9", "198.51.100.10"], relays=["198.51.100.10"])
        reg.remove("198.51.100.9")
        monkeypatch.setattr("meridian.cluster.ClusterConfig.load", lambda: cluster)

        result = resolve_server(reg)
        assert result.ip == "198.51.100.10"
        assert result.user == "root"


class TestMultipleServers:
    """Path 5: multiple servers registered, no selection."""

    def test_multiple_servers_fail(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry("198.51.100.10", "root", "exit-a"))
        reg.add(ServerEntry("198.51.100.11", "root", "exit-b"))
        reg.add(ServerEntry("203.0.113.10", "root", "relay-a"))
        cluster = _topology_cluster(reg, exits=["198.51.100.10", "198.51.100.11"], relays=["203.0.113.10"])
        monkeypatch.setattr("meridian.cluster.ClusterConfig.load", lambda: cluster)

        with pytest.raises(typer.Exit) as exc_info:
            resolve_server(reg)
        assert exc_info.value.exit_code != 0


class TestNoServers:
    """Path 6: empty registry, no explicit IP."""

    def test_no_servers_fail(self, servers_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("meridian.cluster.ClusterConfig.load", lambda: ClusterConfig())
        reg = ServerRegistry(servers_file)
        with pytest.raises(typer.Exit) as exc_info:
            resolve_server(reg)
        assert exc_info.value.exit_code != 0


MACHINE_ID = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def this_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    machine_id_file = tmp_path / "machine-id"
    machine_id_file.write_text(MACHINE_ID + "\n")
    monkeypatch.setattr("meridian.servers.MACHINE_ID_FILE", machine_id_file)
    return MACHINE_ID


class TestLocalMode:
    """Path 3: running on a saved server itself, matched by machine ID."""

    def test_saved_server_with_this_machine_id_runs_locally(self, servers_file: Path, this_machine: str) -> None:
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry(host="198.51.100.10", name="exit-a"))
        reg.add(ServerEntry(host="198.51.100.11", name="home", user="ubuntu", machine_id=this_machine))

        assert resolve_server(reg).ip == "198.51.100.11"
        assert resolve_server(reg).local_mode is True
        assert resolve_server(reg, explicit_ip="home").conn.local_mode is True
        assert resolve_server(reg, explicit_ip="exit-a").local_mode is False

    def test_copied_profiles_on_another_machine_keep_using_ssh(
        self, servers_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        other = tmp_path / "other-machine-id"
        other.write_text("f" * 32 + "\n")
        monkeypatch.setattr("meridian.servers.MACHINE_ID_FILE", other)
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry(host="198.51.100.11", name="home", machine_id=MACHINE_ID))

        result = resolve_server(reg, explicit_ip="home")

        assert result.local_mode is False
        assert result.machine_id == ""

    def test_missing_machine_id_never_matches_an_empty_record(
        self, servers_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("meridian.servers.MACHINE_ID_FILE", tmp_path / "absent")
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry(host="198.51.100.11", name="home"))

        assert reg.find("home") is not None and reg.find("home").runs_here() is False

    def test_an_address_assigned_to_this_machine_runs_locally_without_a_machine_id(
        self, servers_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("meridian.servers.MACHINE_ID_FILE", tmp_path / "absent")
        reg = ServerRegistry(servers_file)
        reg.add(ServerEntry(host="127.0.0.1", name="loop"))

        assert resolve_server(reg, explicit_ip="loop").local_mode is True
        assert resolve_server(reg, explicit_ip="198.51.100.30").local_mode is False

    def test_local_keyword_records_this_machine_for_later_commands(
        self, servers_file: Path, this_machine: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("meridian.resolve.detect_public_ip", lambda: "198.51.100.12")
        reg = ServerRegistry(servers_file)

        result = resolve_server(reg, explicit_ip="local")

        assert (result.ip, result.local_mode, result.machine_id) == ("198.51.100.12", True, this_machine)

    def test_local_target_saves_the_account_running_meridian_not_root(
        self, servers_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("meridian.resolve.detect_public_ip", lambda: "198.51.100.12")
        monkeypatch.setattr("meridian.resolve.current_login", lambda: "ubuntu")
        reg = ServerRegistry(servers_file)

        assert resolve_server(reg, explicit_ip="local").user == "ubuntu"
        assert resolve_server(reg, explicit_ip="local", user="admin").user == "admin"


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
