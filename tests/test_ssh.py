"""Tests for SSH connection helpers and tcp_connect."""

from __future__ import annotations

import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from meridian import ssh
from meridian.core.redaction import REDACTED
from meridian.health import tcp_connect
from meridian.ssh import CommandResult, ServerConnection, SSHError, _verify_host_key


@pytest.fixture(autouse=True)
def _local_process_is_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local-mode tests describe a root process unless they say otherwise."""
    monkeypatch.setattr("meridian.ssh.os.geteuid", lambda: 0, raising=False)


def _done(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _spawned(conn: ServerConnection, command: str, **kwargs: Any) -> tuple[list[str], dict[str, Any]]:
    """Run *command* and return the argv and subprocess kwargs it spawned."""
    with patch("meridian.ssh.subprocess.run", return_value=_done(stdout="ok\n")) as mock_run:
        conn.run(command, **kwargs)
    return mock_run.call_args[0][0], mock_run.call_args[1]


def test_connection_defaults() -> None:
    conn = ServerConnection(ip="198.51.100.10")
    assert conn.ip == "198.51.100.10"
    assert conn.user == "root"
    assert conn.local_mode is False
    assert conn.needs_sudo is False


class TestServerConnectionRun:
    def test_remote_command_uses_strict_batch_ssh(self) -> None:
        cmd, kwargs = _spawned(ServerConnection(ip="198.51.100.11", user="root"), "whoami")
        assert "ssh" in cmd
        assert "root@198.51.100.11" in cmd
        assert "whoami" in cmd
        assert "BatchMode=yes" in cmd
        assert "ConnectTimeout=10" in cmd
        assert "StrictHostKeyChecking=yes" in cmd
        assert kwargs["stdin"] == subprocess.DEVNULL

    @pytest.mark.parametrize(
        ("needs_sudo", "expected"),
        [(False, ["bash", "-c", "echo hello"]), (True, ["sudo", "-n", "bash", "-c", "echo hello"])],
    )
    def test_local_mode_uses_bash(self, needs_sudo: bool, expected: list[str]) -> None:
        conn = ServerConnection(ip="198.51.100.10", local_mode=True)
        conn.needs_sudo = needs_sudo
        assert _spawned(conn, "echo hello")[0] == expected

    def test_control_socket_path_fits_macos_limit_for_ipv6_targets(self, monkeypatch) -> None:
        monkeypatch.setattr(ssh.config, "INSTALL_HOME", Path("/Users/someone/.meridian-v4"))
        opts = ssh.multiplex_opts("a-long-deploy-user", "2001:db8:1234:5678:9abc:def0:1234:5678", 2222)

        path = next(opt.split("=", 1)[1] for opt in opts if opt.startswith("ControlPath="))
        assert len(path) + len(".tECSFEUKn4XON7D8") <= 103
        assert ":" not in Path(path).name

    def test_too_deep_home_disables_multiplexing_instead_of_breaking_ssh(self, monkeypatch) -> None:
        monkeypatch.setattr(ssh.config, "INSTALL_HOME", Path("/Users/someone/" + "nested/" * 12 + ".meridian"))

        assert ssh.multiplex_opts("root", "198.51.100.10", 22) == []

    def test_remote_can_pin_identity_file_without_multiplexing(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", user="deploy", identity_file="/tmp/meridian_ed25519")
        cmd, _ = _spawned(conn, "whoami")

        assert "-i" in cmd
        assert "/tmp/meridian_ed25519" in cmd
        assert "IdentitiesOnly=yes" in cmd
        assert conn.multiplex is False

    def test_remote_password_uses_askpass_without_batch_mode(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", user="root", password="secret")
        with patch("meridian.ssh.ensure_askpass_script", return_value=Path("/tmp/askpass")):
            cmd, kwargs = _spawned(conn, "whoami")

        assert "BatchMode=no" in cmd
        assert "StrictHostKeyChecking=yes" in cmd
        assert conn.multiplex is False
        assert kwargs["env"]["SSH_ASKPASS"] == "/tmp/askpass"
        assert kwargs["env"]["MERIDIAN_SSH_PASSWORD"] == "secret"

    def test_returns_command_result_with_metadata(self) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        with patch("meridian.ssh.subprocess.run", return_value=_done(stdout="ok\n")):
            result = conn.run("echo ok", operation_name="smoke")

        assert isinstance(result, CommandResult)
        assert result.stdout == "ok\n"
        assert result.operation_name == "smoke"
        assert result.attempts == 1
        assert result.timed_out is False

    def test_retries_until_ok_code(self) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        with patch("meridian.ssh.subprocess.run", side_effect=[_done(1, stderr="nope"), _done(stdout="ok")]) as run:
            result = conn.run("flaky", retries=2, retry_delay=0)

        assert result.returncode == 0
        assert result.attempts == 2
        assert run.call_count == 2

    @pytest.mark.parametrize(
        ("command", "kwargs", "script"),
        [
            (
                "printenv FOO",
                {"cwd": "/opt/app", "env": {"FOO": "bar baz"}},
                "cd /opt/app && export FOO='bar baz' && printenv FOO",
            ),
            # The export must cover every part of a compound command.
            (
                "apt-get update && apt-get install -y docker",
                {"env": {"DEBIAN_FRONTEND": "noninteractive"}},
                "export DEBIAN_FRONTEND=noninteractive && apt-get update && apt-get install -y docker",
            ),
        ],
    )
    def test_cwd_and_env_are_applied_before_command(self, command: str, kwargs: dict, script: str) -> None:
        conn = ServerConnection(ip="198.51.100.10", local_mode=True)
        assert _spawned(conn, command, **kwargs)[0] == ["bash", "-c", script]

    def test_invalid_env_name_raises(self) -> None:
        with pytest.raises(ValueError):
            ServerConnection(ip="198.51.100.10").run("true", env={"BAD-NAME": "x"})

    def test_timeout_returns_result(self) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        with patch("meridian.ssh.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=1)):
            result = conn.run("sleep 10", timeout=1)

        assert result.returncode == 124
        assert result.timed_out is True

    def test_put_text_uses_stdin_not_shell_interpolation(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", user="root")
        with patch("meridian.ssh.subprocess.run", return_value=_done()) as mock_run:
            result = conn.put_text(
                "/etc/meridian/secret.env", "TOKEN=super-secret\n", mode="600", atomic=False, sensitive=True
            )

        assert result.returncode == 0
        write_call = next(call for call in mock_run.call_args_list if call[1].get("input") == b"TOKEN=super-secret\n")
        assert "TOKEN=super-secret" not in " ".join(write_call[0][0])
        assert "/etc/meridian/secret.env" not in result.redacted_command
        assert "<sensitive path>" in result.redacted_command

    def test_sensitive_atomic_write_precreates_temp_with_private_mode(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", user="root")
        with patch("meridian.ssh.subprocess.run", return_value=_done()) as mock_run:
            result = conn.put_text("/etc/meridian/secret.env", "TOKEN=super-secret\n", mode="600", sensitive=True)

        assert result.returncode == 0
        commands = [" ".join(call[0][0]) for call in mock_run.call_args_list]
        assert any("install -m 600 /dev/null /etc/meridian/secret.env.tmp." in command for command in commands)
        write_index = next(i for i, call in enumerate(mock_run.call_args_list) if call[1].get("input"))
        create_index = next(i for i, command in enumerate(commands) if "install -m 600 /dev/null" in command)
        assert create_index < write_index


class TestStalledConnection:
    """A stalled control connection is dropped so the next command dials a fresh one (no real ssh)."""

    @pytest.fixture
    def socket_path(self, monkeypatch: pytest.MonkeyPatch) -> Any:
        home = Path(tempfile.mkdtemp(prefix="mh", dir="/tmp"))  # short enough for a control socket
        monkeypatch.setattr(ssh.config, "INSTALL_HOME", home)
        path = ssh.control_path("root", "198.51.100.10", 22)
        assert path is not None
        path.parent.mkdir(parents=True)
        path.touch()  # stands in for the master's socket
        yield path
        shutil.rmtree(home, ignore_errors=True)

    @staticmethod
    def _exits(mock_run: MagicMock) -> list[list[str]]:
        return [call[0][0] for call in mock_run.call_args_list if "-O" in call[0][0]]

    @pytest.mark.parametrize("identity_file", ["", "/tmp/meridian_ed25519"], ids=["multiplexed", "pinned-key"])
    def test_every_ssh_command_sends_keepalives(self, identity_file: str) -> None:
        cmd, _ = _spawned(ServerConnection(ip="198.51.100.10", identity_file=identity_file), "whoami")
        assert "ServerAliveInterval=5" in cmd and "ServerAliveCountMax=3" in cmd

    @pytest.mark.parametrize(
        "outcome",
        [subprocess.TimeoutExpired(cmd="ssh", timeout=1), _done(255, stderr="Broken pipe")],
        ids=["timeout", "transport-255"],
    )
    def test_transport_failure_asks_the_master_to_exit_and_frees_its_socket(
        self, socket_path: Path, outcome: Any
    ) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        with patch("meridian.ssh.subprocess.run", side_effect=[outcome, _done()]) as mock_run:
            result = conn.run("cat /etc/hostname", timeout=1)

        assert result.returncode in {124, 255}
        [exit_cmd] = self._exits(mock_run)
        assert exit_cmd[:2] == ["ssh", "-o"] and exit_cmd[2] == f"ControlPath={socket_path}"
        assert exit_cmd[3:] == ["-O", "exit", "root@198.51.100.10"]
        assert not socket_path.exists()

    def test_wedged_master_that_ignores_exit_still_loses_its_socket(self, socket_path: Path) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        stall = subprocess.TimeoutExpired(cmd="ssh", timeout=1)
        with patch("meridian.ssh.subprocess.run", side_effect=[stall, stall]):
            conn.run("cat /etc/hostname", timeout=1)

        assert not socket_path.exists()

    def test_retry_after_a_dropped_master_reconnects(self, socket_path: Path) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        replies = [_done(255, stderr="mux_client_request_session: read from master failed"), _done(), _done(0, "ok")]
        with patch("meridian.ssh.subprocess.run", side_effect=replies) as mock_run:
            result = conn.run("echo ok", retries=2)

        assert result.ok and result.attempts == 2
        assert [call[0][0][-1] for call in mock_run.call_args_list] == ["echo ok", "root@198.51.100.10", "echo ok"]

    @pytest.mark.parametrize("returncode", [0, 1, 127])
    def test_command_failures_keep_the_master(self, socket_path: Path, returncode: int) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        with patch("meridian.ssh.subprocess.run", return_value=_done(returncode)) as mock_run:
            conn.run("false")

        assert self._exits(mock_run) == [] and socket_path.exists()

    def test_local_mode_has_no_master_to_drop(self, socket_path: Path) -> None:
        conn = ServerConnection(ip="198.51.100.10", local_mode=True)
        with patch("meridian.ssh.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="bash", timeout=1)) as run:
            conn.run("sleep 10", timeout=1)

        assert run.call_count == 1 and socket_path.exists()

    def test_file_write_that_loses_the_connection_drops_the_master(self, socket_path: Path) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        with patch("meridian.ssh.subprocess.run", side_effect=[_done(255), _done()]) as mock_run:
            result = conn.put_text("/etc/hostname", "exit\n", atomic=False)

        assert result.returncode == 255
        assert len(self._exits(mock_run)) == 1 and not socket_path.exists()


class TestTcpConnect:
    @pytest.mark.parametrize(
        ("host", "port", "kwargs", "timeout"),
        [("198.51.100.10", 443, {}, 5), ("198.51.100.10", 80, {"timeout": 10}, 10), ("2001:db8::1", 443, {}, 5)],
    )
    def test_connects_and_closes(self, host: str, port: int, kwargs: dict, timeout: int) -> None:
        sock = MagicMock()
        with patch("socket.create_connection", return_value=sock) as create:
            assert tcp_connect(host, port, **kwargs) is True
        create.assert_called_once_with((host, port), timeout=timeout)
        sock.close.assert_called_once()

    @pytest.mark.parametrize("error", [ConnectionRefusedError, socket.timeout, OSError("Network unreachable")])
    def test_returns_false_on_socket_errors(self, error: Any) -> None:
        with patch("socket.create_connection", side_effect=error):
            assert tcp_connect("198.51.100.10", 443) is False


class TestCheckSSH:
    def test_local_mode_skips_check(self) -> None:
        ServerConnection(ip="198.51.100.10", local_mode=True).check_ssh()

    def test_local_non_root_without_passwordless_sudo_fails_with_a_working_command(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", local_mode=True)
        conn.needs_sudo = True
        denied = _done(1, stderr="sudo: a password is required")
        with patch("meridian.ssh.subprocess.run", return_value=denied), pytest.raises(SSHError) as exc_info:
            conn.check_ssh()

        assert exc_info.value.category == "user"
        assert 'sudo "$(command -v meridian)"' in exc_info.value.hint
        assert "NOPASSWD" in exc_info.value.hint

    @patch("meridian.ssh._host_key_known", return_value=True)
    def test_remote_password_sudo_is_not_reported_as_an_ssh_key_problem(self, _mock_hk: Any) -> None:
        conn = ServerConnection(ip="198.51.100.10", user="alice")
        with (
            patch.object(conn, "run", return_value=_done(1, stderr="sudo: a password is required")),
            pytest.raises(SSHError, match="sudo asks for a password") as exc_info,
        ):
            conn.check_ssh()

        assert "alice ALL=(ALL) NOPASSWD:ALL" in exc_info.value.hint

    @pytest.mark.parametrize("key_known", [True, False])
    def test_reachable_server_passes(self, key_known: bool) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        with (
            patch("meridian.ssh._host_key_known", return_value=key_known),
            patch("meridian.ssh._verify_host_key", return_value=True),
            patch.object(conn, "run", return_value=_done(stdout="ok\n")),
        ):
            conn.check_ssh()

    @pytest.mark.parametrize(
        ("run", "match"),
        [
            ({"return_value": _done(255, stderr="Permission denied")}, None),
            # run() turns TimeoutExpired into returncode 124.
            ({"return_value": _done(124, stderr="Command timed out after 10s")}, "timed out"),
            ({"side_effect": FileNotFoundError}, None),
        ],
        ids=["denied", "timeout", "no-ssh-binary"],
    )
    @patch("meridian.ssh._host_key_known", return_value=True)
    def test_unreachable_server_raises(self, _mock_hk: Any, run: dict, match: str | None) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        with patch.object(conn, "run", **run), pytest.raises(SSHError, match=match):
            conn.check_ssh()

    @patch("meridian.ssh._verify_host_key", return_value=False)
    @patch("meridian.ssh._host_key_known", return_value=False)
    def test_unknown_host_key_rejected_exits(self, _mock_hk: Any, _mock_vhk: Any) -> None:
        with pytest.raises(SSHError):
            ServerConnection(ip="198.51.100.10").check_ssh()


_ALL_KEYS = (
    "198.51.100.10 ssh-rsa AAAAB3NzaC1yc2EAAA...\n"
    "198.51.100.10 ecdsa-sha2-nistp256 AAAAE2VjZHNh...\n"
    "198.51.100.10 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA...\n"
)


def _scan(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
    if cmd[0] == "ssh-keyscan":
        return _done(stdout=_ALL_KEYS)
    if cmd[0] == "ssh-keygen":
        return _done(stdout="256 SHA256:abc... 198.51.100.10 (ED25519)\n")
    raise ValueError(f"Unexpected command: {cmd}")


def _verify_with_tty(answer: str | None) -> bool:
    """Run _verify_host_key with /dev/tty answering *answer* (None: no terminal); other files are real."""
    real_open = open

    def fake_open(path: Any, *args: Any, **kwargs: Any) -> Any:
        if str(path) != "/dev/tty":
            return real_open(path, *args, **kwargs)
        if answer is None:
            raise OSError("No TTY")
        tty = MagicMock()
        tty.readline.return_value = f"{answer}\n"
        tty.__enter__.return_value = tty
        return tty

    with patch("meridian.ssh.subprocess.run", side_effect=_scan), patch("builtins.open", side_effect=fake_open):
        return _verify_host_key("198.51.100.10")


class TestVerifyHostKey:
    @pytest.fixture
    def known_hosts(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        (tmp_path / ".ssh").mkdir(mode=0o700)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        return tmp_path / ".ssh" / "known_hosts"

    def test_accepted_key_writes_only_the_preferred_ed25519_key(self, known_hosts: Path) -> None:
        assert _verify_with_tty("y") is True

        lines = known_hosts.read_text().strip().splitlines()
        assert len(lines) == 1
        assert "ssh-ed25519" in lines[0]

    def test_rejected_key_returns_false_and_writes_nothing(self, known_hosts: Path) -> None:
        assert _verify_with_tty("n") is False
        assert not known_hosts.exists()

    def test_no_tty_fails_instead_of_auto_accept(self) -> None:
        with pytest.raises(SSHError, match="no terminal available"):
            _verify_with_tty(None)

    def test_unwritable_known_hosts_is_a_user_error_not_a_traceback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".ssh" / "known_hosts").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        ui = MagicMock()
        ui.host_key_prompt.return_value = True

        with (
            patch("meridian.ssh.subprocess.run", return_value=_done(stdout=_ALL_KEYS)),
            pytest.raises(SSHError) as excinfo,
        ):
            _verify_host_key("198.51.100.10", ui=ui)

        assert excinfo.value.category == "user"
        assert "ssh-keyscan 198.51.100.10" in (excinfo.value.hint or "")


@pytest.mark.parametrize(("euid", "needs_sudo"), [(0, False), (1000, True)])
def test_local_sudo_follows_the_process_uid_not_the_saved_user(
    monkeypatch: pytest.MonkeyPatch, euid: int, needs_sudo: bool
) -> None:
    monkeypatch.setattr("meridian.ssh.os.geteuid", lambda: euid)
    conn = ServerConnection(ip="198.51.100.1", user="root", local_mode=True)
    assert conn.needs_sudo is needs_sudo


@pytest.mark.parametrize("sudo", [None, True])
def test_local_root_process_never_wraps_in_sudo_whatever_the_saved_user(
    monkeypatch: pytest.MonkeyPatch, sudo: bool | None
) -> None:
    """A root process on a minimal server may have no sudo at all (#77)."""
    monkeypatch.setattr("meridian.ssh.os.geteuid", lambda: 0)
    conn = ServerConnection(ip="198.51.100.1", user="admin", local_mode=True)
    with patch("meridian.ssh.subprocess.run", return_value=_done()) as run:
        conn.run("true", sudo=sudo)
        conn.put_bytes("/tmp/meridian-test", b"x")
    argv = [call.args[0] for call in run.call_args_list]
    assert argv and all(cmd[0] != "sudo" for cmd in argv)


def _logged_command(command: str, **kwargs: Any) -> str:
    conn = ServerConnection(ip="198.51.100.10", local_mode=True)
    with patch("meridian.ssh.subprocess.run", return_value=_done()):
        return conn.run(command, **kwargs).redacted_command


@pytest.mark.parametrize(
    ("command", "secrets", "redacted_keys"),
    [
        (
            "printf 'SECRET_KEY=eyJhbGciOiJIUzI1NiJ9.abc123' > /opt/remnanode/.env",
            ["eyJhbGciOiJIUzI1NiJ9"],
            ["SECRET_KEY"],
        ),
        ("POSTGRES_PASSWORD=supersecret123 docker compose up", ["supersecret123"], ["POSTGRES_PASSWORD"]),
        (
            "REMNAWAVE_API_TOKEN=abc123 DATABASE_URL=postgres://user:pass@db/app docker compose up",
            ["abc123", "user:pass"],
            ["REMNAWAVE_API_TOKEN", "DATABASE_URL"],
        ),
        ("JWT_AUTH_SECRET=abc123 JWT_API_TOKENS_SECRET=def456", ["abc123", "def456"], []),
    ],
)
def test_logged_commands_redact_secrets(command: str, secrets: list[str], redacted_keys: list[str]) -> None:
    logged = _logged_command(command)
    for secret in secrets:
        assert secret not in logged
    for key in redacted_keys:
        assert f"{key}={REDACTED}" in logged


def test_logged_commands_keep_non_secret_text() -> None:
    cmd = "docker ps --format '{{.Names}}'"
    assert _logged_command(cmd) == cmd


def test_sensitive_commands_are_never_logged() -> None:
    assert _logged_command("echo plain", sensitive=True) == "<sensitive command>"
