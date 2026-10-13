"""Tests for SSH connection helpers and tcp_connect."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, mock_open, patch

import pytest

from meridian.core.redaction import REDACTED
from meridian.health import tcp_connect
from meridian.ssh import CommandResult, ServerConnection, SSHError, _verify_host_key


@pytest.fixture(autouse=True)
def _local_process_is_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local-mode tests describe a root process unless they say otherwise."""
    monkeypatch.setattr("meridian.ssh.os.geteuid", lambda: 0, raising=False)


class TestServerConnectionInit:
    def test_defaults(self) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        assert conn.ip == "198.51.100.10"
        assert conn.user == "root"
        assert conn.local_mode is False
        assert conn.needs_sudo is False

    def test_custom_user(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", user="ubuntu")
        assert conn.user == "ubuntu"

    def test_local_mode(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", local_mode=True)
        assert conn.local_mode is True


class TestServerConnectionRun:
    def test_remote_command_uses_ssh(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", user="root")
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n", stderr="")
            conn.run("echo hello")
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "ssh" in cmd
            assert "root@198.51.100.10" in cmd
            assert "echo hello" in cmd

    def test_local_mode_uses_bash(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", local_mode=True)
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n", stderr="")
            conn.run("echo hello")
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert cmd == ["bash", "-c", "echo hello"]

    def test_local_mode_needs_sudo(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", local_mode=True)
        conn.needs_sudo = True
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n", stderr="")
            conn.run("cat /etc/meridian/node.yml")
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert cmd == ["sudo", "-n", "bash", "-c", "cat /etc/meridian/node.yml"]

    def test_remote_includes_ssh_opts(self) -> None:
        conn = ServerConnection(ip="198.51.100.11", user="deploy")
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            conn.run("whoami")
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "BatchMode=yes" in cmd
            assert "ConnectTimeout=10" in cmd
            assert "StrictHostKeyChecking=yes" in cmd

    def test_control_socket_path_fits_macos_limit_for_ipv6_targets(self, monkeypatch) -> None:
        from pathlib import Path

        from meridian import ssh

        monkeypatch.setattr(ssh, "MERIDIAN_HOME", Path("/Users/someone/.meridian-v4"))
        opts = ssh.multiplex_opts("a-long-deploy-user", "2001:db8:1234:5678:9abc:def0:1234:5678", 2222)

        path = next(opt.split("=", 1)[1] for opt in opts if opt.startswith("ControlPath="))
        assert len(path) + len(".tECSFEUKn4XON7D8") <= 103
        assert ":" not in Path(path).name

    def test_too_deep_home_disables_multiplexing_instead_of_breaking_ssh(self, monkeypatch) -> None:
        from pathlib import Path

        from meridian import ssh

        monkeypatch.setattr(ssh, "MERIDIAN_HOME", Path("/Users/someone/" + "nested/" * 12 + ".meridian"))

        assert ssh.multiplex_opts("root", "198.51.100.10", 22) == []

    def test_remote_can_pin_identity_file(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", user="deploy", identity_file="/tmp/meridian_ed25519")
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            conn.run("whoami")

        cmd = mock_run.call_args[0][0]
        assert "-i" in cmd
        assert "/tmp/meridian_ed25519" in cmd
        assert "IdentitiesOnly=yes" in cmd

    def test_remote_password_uses_askpass_without_batch_mode(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", user="root", password="secret")
        with (
            patch("meridian.ssh.ensure_askpass_script", return_value=Path("/tmp/askpass")),
            patch("meridian.ssh.subprocess.run") as mock_run,
        ):
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            conn.run("whoami")

        cmd = mock_run.call_args[0][0]
        kwargs = mock_run.call_args[1]
        assert "BatchMode=no" in cmd
        assert "StrictHostKeyChecking=yes" in cmd
        assert conn.multiplex is False
        assert kwargs["env"]["SSH_ASKPASS"] == "/tmp/askpass"
        assert kwargs["env"]["MERIDIAN_SSH_PASSWORD"] == "secret"

    def test_remote_identity_file_disables_multiplexing(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", user="root", identity_file="/tmp/meridian_ed25519")

        assert conn.multiplex is False

    def test_stdin_devnull(self) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            conn.run("echo test")
            kwargs = mock_run.call_args[1]
            assert kwargs["stdin"] == subprocess.DEVNULL

    def test_returns_command_result_with_metadata(self) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="ok\n", stderr="")
            result = conn.run("echo ok", operation_name="smoke")

        assert isinstance(result, CommandResult)
        assert result.stdout == "ok\n"
        assert result.operation_name == "smoke"
        assert result.attempts == 1
        assert result.timed_out is False

    def test_retries_until_ok_code(self) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.side_effect = [
                subprocess.CompletedProcess(args=["ssh"], returncode=1, stdout="", stderr="nope"),
                subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="ok", stderr=""),
            ]
            result = conn.run("flaky", retries=2, retry_delay=0)

        assert result.returncode == 0
        assert result.attempts == 2
        assert mock_run.call_count == 2

    def test_cwd_and_env_are_applied_before_command(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", local_mode=True)
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            conn.run("printenv FOO", cwd="/opt/app", env={"FOO": "bar baz"})

        cmd = mock_run.call_args[0][0]
        assert cmd == ["bash", "-c", "cd /opt/app && export FOO='bar baz' && printenv FOO"]

    def test_env_applies_to_compound_commands(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", local_mode=True)
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            conn.run("apt-get update && apt-get install -y docker", env={"DEBIAN_FRONTEND": "noninteractive"})

        cmd = mock_run.call_args[0][0]
        assert cmd == [
            "bash",
            "-c",
            "export DEBIAN_FRONTEND=noninteractive && apt-get update && apt-get install -y docker",
        ]

    def test_invalid_env_name_raises(self) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        with pytest.raises(ValueError):
            conn.run("true", env={"BAD-NAME": "x"})

    def test_timeout_returns_result(self) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        with patch("meridian.ssh.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=1)):
            result = conn.run("sleep 10", timeout=1)

        assert result.returncode == 124
        assert result.timed_out is True

    def test_put_text_uses_stdin_not_shell_interpolation(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", user="root")
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            result = conn.put_text(
                "/etc/meridian/secret.env",
                "TOKEN=super-secret\n",
                mode="600",
                atomic=False,
                sensitive=True,
            )

        assert result.returncode == 0
        write_call = next(call for call in mock_run.call_args_list if call[1].get("input") == b"TOKEN=super-secret\n")
        cmd = write_call[0][0]
        kwargs = write_call[1]
        assert "TOKEN=super-secret" not in " ".join(cmd)
        assert kwargs["input"] == b"TOKEN=super-secret\n"
        assert "/etc/meridian/secret.env" not in result.redacted_command
        assert "<sensitive path>" in result.redacted_command

    def test_sensitive_atomic_write_precreates_temp_with_private_mode(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", user="root")
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            result = conn.put_text(
                "/etc/meridian/secret.env",
                "TOKEN=super-secret\n",
                mode="600",
                sensitive=True,
            )

        assert result.returncode == 0
        commands = [" ".join(call[0][0]) for call in mock_run.call_args_list]
        assert any("install -m 600 /dev/null /etc/meridian/secret.env.tmp." in command for command in commands)
        write_index = next(i for i, call in enumerate(mock_run.call_args_list) if call[1].get("input"))
        create_index = next(i for i, command in enumerate(commands) if "install -m 600 /dev/null" in command)
        assert create_index < write_index


class TestTcpConnect:
    def test_returns_true_on_success(self) -> None:
        mock_conn = MagicMock()
        with patch("socket.create_connection", return_value=mock_conn):
            assert tcp_connect("198.51.100.10", 443) is True
            mock_conn.close.assert_called_once()

    def test_returns_false_on_connection_refused(self) -> None:
        with patch("socket.create_connection", side_effect=ConnectionRefusedError):
            assert tcp_connect("198.51.100.10", 443) is False

    def test_returns_false_on_timeout(self) -> None:
        import socket

        with patch("socket.create_connection", side_effect=socket.timeout):
            assert tcp_connect("198.51.100.10", 443) is False

    def test_returns_false_on_os_error(self) -> None:
        with patch("socket.create_connection", side_effect=OSError("Network unreachable")):
            assert tcp_connect("198.51.100.10", 443) is False

    def test_custom_timeout(self) -> None:
        mock_conn = MagicMock()
        with patch("socket.create_connection", return_value=mock_conn) as mock_create:
            tcp_connect("198.51.100.10", 80, timeout=10)
            mock_create.assert_called_once_with(("198.51.100.10", 80), timeout=10)

    def test_ipv6_address(self) -> None:
        mock_conn = MagicMock()
        with patch("socket.create_connection", return_value=mock_conn) as mock_create:
            assert tcp_connect("2001:db8::1", 443) is True
            mock_create.assert_called_once_with(("2001:db8::1", 443), timeout=5)


class TestCheckSSH:
    def test_local_mode_skips_check(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", local_mode=True)
        # Should return without doing anything
        conn.check_ssh()  # no exception

    def test_local_non_root_without_passwordless_sudo_fails_with_a_working_command(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", local_mode=True)
        conn.needs_sudo = True
        denied = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="sudo: a password is required")
        with patch("meridian.ssh.subprocess.run", return_value=denied), pytest.raises(SSHError) as exc_info:
            conn.check_ssh()

        assert exc_info.value.category == "user"
        assert 'sudo "$(command -v meridian)"' in exc_info.value.hint
        assert "NOPASSWD" in exc_info.value.hint

    @patch("meridian.ssh._host_key_known", return_value=True)
    def test_remote_password_sudo_is_not_reported_as_an_ssh_key_problem(self, _mock_hk: Any) -> None:
        conn = ServerConnection(ip="198.51.100.10", user="alice")
        with patch.object(conn, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="sudo: a password is required"
            )
            with pytest.raises(SSHError, match="sudo asks for a password") as exc_info:
                conn.check_ssh()

        assert "alice ALL=(ALL) NOPASSWD:ALL" in exc_info.value.hint

    @patch("meridian.ssh._host_key_known", return_value=True)
    def test_ssh_success(self, _mock_hk: Any) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        with patch.object(conn, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n", stderr="")
            conn.check_ssh()  # should not raise

    @patch("meridian.ssh._host_key_known", return_value=True)
    def test_ssh_failure_exits(self, _mock_hk: Any) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        with patch.object(conn, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=255, stdout="", stderr="Permission denied"
            )
            with pytest.raises(SSHError):
                conn.check_ssh()

    @patch("meridian.ssh._host_key_known", return_value=True)
    def test_ssh_timeout_exits(self, _mock_hk: Any) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        with patch.object(conn, "run") as mock_run:
            # run() converts TimeoutExpired to returncode=124 internally
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=124, stdout="", stderr="Command timed out after 10s"
            )
            with pytest.raises(SSHError, match="timed out"):
                conn.check_ssh()

    @patch("meridian.ssh._host_key_known", return_value=True)
    def test_ssh_not_found_exits(self, _mock_hk: Any) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        with patch.object(conn, "run", side_effect=FileNotFoundError):
            with pytest.raises(SSHError):
                conn.check_ssh()

    @patch("meridian.ssh._verify_host_key", return_value=False)
    @patch("meridian.ssh._host_key_known", return_value=False)
    def test_unknown_host_key_rejected_exits(self, _mock_hk: Any, _mock_vhk: Any) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        with pytest.raises(SSHError):
            conn.check_ssh()

    @patch("meridian.ssh._verify_host_key", return_value=True)
    @patch("meridian.ssh._host_key_known", return_value=False)
    def test_unknown_host_key_accepted_continues(self, _mock_hk: Any, _mock_vhk: Any) -> None:
        conn = ServerConnection(ip="198.51.100.10")
        with patch.object(conn, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n", stderr="")
            conn.check_ssh()  # should not raise


class TestVerifyHostKey:
    """Tests for _verify_host_key — host key scanning, verification, and storage."""

    SCAN_OUTPUT = (
        "198.51.100.10 ssh-rsa AAAAB3NzaC1yc2EAAA...\n"
        "198.51.100.10 ecdsa-sha2-nistp256 AAAAE2VjZHNh...\n"
        "198.51.100.10 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA...\n"
    )

    def test_writes_only_preferred_key_not_all(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Only the verified (preferred) key should be written to known_hosts."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir(mode=0o700)
        known_hosts = ssh_dir / "known_hosts"
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        scan_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=self.SCAN_OUTPUT, stderr="")
        fingerprint_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="256 SHA256:abc... 198.51.100.10 (ED25519)\n", stderr=""
        )

        def mock_subprocess_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if cmd[0] == "ssh-keyscan":
                return scan_result
            if cmd[0] == "ssh-keygen":
                return fingerprint_result
            raise ValueError(f"Unexpected command: {cmd}")

        with (
            patch("meridian.ssh.subprocess.run", side_effect=mock_subprocess_run),
            patch("builtins.open", mock_open()) as tty_mock,
        ):
            # Simulate user typing "y" at the TTY prompt
            tty_handle = MagicMock()
            tty_handle.readline.return_value = "y\n"
            tty_mock.return_value.__enter__ = lambda s: tty_handle

            # But we need the real file write for known_hosts — use a different approach
            pass

        # Use a simpler approach: patch /dev/tty read, let file writes go through
        with patch("meridian.ssh.subprocess.run", side_effect=mock_subprocess_run):
            # Patch the TTY open to return "y"
            original_open = open

            def patched_open(path: Any, *args: Any, **kwargs: Any) -> Any:
                if str(path) == "/dev/tty":
                    m = MagicMock()
                    m.readline.return_value = "y\n"
                    m.__enter__ = lambda s: m
                    m.__exit__ = lambda s, *a: None
                    return m
                return original_open(path, *args, **kwargs)

            with patch("builtins.open", side_effect=patched_open):
                result = _verify_host_key("198.51.100.10")

        assert result is True
        assert known_hosts.exists()
        lines = known_hosts.read_text().strip().splitlines()
        # Only 1 key written (the preferred ed25519), not all 3
        assert len(lines) == 1
        assert "ssh-ed25519" in lines[0]

    def test_unwritable_known_hosts_is_a_user_error_not_a_traceback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".ssh" / "known_hosts").mkdir(parents=True)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        scan = subprocess.CompletedProcess(args=[], returncode=0, stdout=self.SCAN_OUTPUT, stderr="")
        ui = MagicMock()
        ui.host_key_prompt.return_value = True

        with patch("meridian.ssh.subprocess.run", return_value=scan), pytest.raises(SSHError) as excinfo:
            _verify_host_key("198.51.100.10", ui=ui)

        assert excinfo.value.category == "user"
        assert "ssh-keyscan 198.51.100.10" in (excinfo.value.hint or "")

    def test_prefers_ed25519_over_rsa(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """ed25519 should be preferred over ecdsa and rsa."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir(mode=0o700)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        scan_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=self.SCAN_OUTPUT, stderr="")
        fp_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="256 SHA256:xyz... (ED25519)\n", stderr=""
        )

        def mock_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if cmd[0] == "ssh-keyscan":
                return scan_result
            return fp_result

        original_open = open

        def patched_open(path: Any, *args: Any, **kwargs: Any) -> Any:
            if str(path) == "/dev/tty":
                m = MagicMock()
                m.readline.return_value = "y\n"
                m.__enter__ = lambda s: m
                m.__exit__ = lambda s, *a: None
                return m
            return original_open(path, *args, **kwargs)

        with (
            patch("meridian.ssh.subprocess.run", side_effect=mock_run),
            patch("builtins.open", side_effect=patched_open),
        ):
            _verify_host_key("198.51.100.10")

        known_hosts = ssh_dir / "known_hosts"
        content = known_hosts.read_text()
        assert "ssh-ed25519" in content
        assert "ssh-rsa" not in content
        assert "ecdsa" not in content

    def test_no_tty_fails_instead_of_auto_accept(self) -> None:
        """Non-interactive mode should refuse host key, not silently accept."""
        scan_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="198.51.100.10 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA...\n",
            stderr="",
        )
        fp_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="256 SHA256:abc... (ED25519)\n", stderr=""
        )

        def mock_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if cmd[0] == "ssh-keyscan":
                return scan_result
            return fp_result

        original_open = open

        def patched_open(path: Any, *args: Any, **kwargs: Any) -> Any:
            if str(path) == "/dev/tty":
                raise OSError("No TTY")
            return original_open(path, *args, **kwargs)

        with (
            patch("meridian.ssh.subprocess.run", side_effect=mock_run),
            patch("builtins.open", side_effect=patched_open),
        ):
            with pytest.raises(SSHError, match="no terminal available"):
                _verify_host_key("198.51.100.10")

    def test_user_rejects_key_returns_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """User answering 'n' should return False without writing anything."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir(mode=0o700)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        scan_result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="198.51.100.10 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA...\n",
            stderr="",
        )
        fp_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="256 SHA256:abc... (ED25519)\n", stderr=""
        )

        def mock_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if cmd[0] == "ssh-keyscan":
                return scan_result
            return fp_result

        original_open = open

        def patched_open(path: Any, *args: Any, **kwargs: Any) -> Any:
            if str(path) == "/dev/tty":
                m = MagicMock()
                m.readline.return_value = "n\n"
                m.__enter__ = lambda s: m
                m.__exit__ = lambda s, *a: None
                return m
            return original_open(path, *args, **kwargs)

        with (
            patch("meridian.ssh.subprocess.run", side_effect=mock_run),
            patch("builtins.open", side_effect=patched_open),
        ):
            result = _verify_host_key("198.51.100.10")

        assert result is False
        known_hosts = ssh_dir / "known_hosts"
        assert not known_hosts.exists()


class TestLocalConnection:
    @pytest.mark.parametrize(("euid", "needs_sudo"), [(0, False), (1000, True)])
    def test_local_sudo_follows_the_process_uid_not_the_saved_user(
        self, monkeypatch: pytest.MonkeyPatch, euid: int, needs_sudo: bool
    ) -> None:
        monkeypatch.setattr("meridian.ssh.os.geteuid", lambda: euid)
        conn = ServerConnection(ip="198.51.100.1", user="root", local_mode=True)
        assert conn.needs_sudo is needs_sudo


def _logged_command(command: str) -> str:
    conn = ServerConnection(ip="198.51.100.10", local_mode=True)
    with patch("meridian.ssh.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        return conn.run(command).redacted_command


class TestRedactCommand:
    def test_redacts_secret_key(self) -> None:
        result = _logged_command("printf 'SECRET_KEY=eyJhbGciOiJIUzI1NiJ9.abc123' > /opt/remnanode/.env")
        assert "eyJhbGciOiJIUzI1NiJ9" not in result
        assert f"SECRET_KEY={REDACTED}" in result

    def test_redacts_postgres_password(self) -> None:
        result = _logged_command("POSTGRES_PASSWORD=supersecret123 docker compose up")
        assert "supersecret123" not in result
        assert f"POSTGRES_PASSWORD={REDACTED}" in result

    def test_redacts_token_and_database_url(self) -> None:
        result = _logged_command(
            "REMNAWAVE_API_TOKEN=abc123 DATABASE_URL=postgres://user:pass@db/app docker compose up"
        )
        assert "abc123" not in result
        assert "user:pass" not in result
        assert f"REMNAWAVE_API_TOKEN={REDACTED}" in result
        assert f"DATABASE_URL={REDACTED}" in result

    def test_redacts_jwt_secrets(self) -> None:
        result = _logged_command("JWT_AUTH_SECRET=abc123 JWT_API_TOKENS_SECRET=def456")
        assert "abc123" not in result
        assert "def456" not in result

    def test_preserves_non_secret_commands(self) -> None:
        cmd = "docker ps --format '{{.Names}}'"
        assert _logged_command(cmd) == cmd

    def test_sensitive_commands_are_never_logged(self) -> None:
        conn = ServerConnection(ip="198.51.100.10", local_mode=True)
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            result = conn.run("echo plain", sensitive=True)
        assert result.redacted_command == "<sensitive command>"
