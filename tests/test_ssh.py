"""Tests for SSH connection helpers and tcp_connect."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, mock_open, patch

import pytest

from meridian.ssh import CommandResult, ServerConnection, SSHError, _verify_host_key, scp_host, tcp_connect


class TestServerConnectionInit:
    def test_defaults(self) -> None:
        conn = ServerConnection(ip="1.2.3.4")
        assert conn.ip == "1.2.3.4"
        assert conn.user == "root"
        assert conn.local_mode is False
        assert conn.needs_sudo is False

    def test_custom_user(self) -> None:
        conn = ServerConnection(ip="1.2.3.4", user="ubuntu")
        assert conn.user == "ubuntu"

    def test_local_mode(self) -> None:
        conn = ServerConnection(ip="1.2.3.4", local_mode=True)
        assert conn.local_mode is True

    def test_scp_host_ipv4(self) -> None:
        conn = ServerConnection(ip="1.2.3.4")
        assert conn._scp_host == "1.2.3.4"

    def test_scp_host_ipv6_bracketed(self) -> None:
        conn = ServerConnection(ip="2001:db8::1")
        assert conn._scp_host == "[2001:db8::1]"


class TestServerConnectionRun:
    def test_remote_command_uses_ssh(self) -> None:
        conn = ServerConnection(ip="1.2.3.4", user="root")
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n", stderr="")
            conn.run("echo hello")
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "ssh" in cmd
            assert "root@1.2.3.4" in cmd
            assert "echo hello" in cmd

    def test_local_mode_uses_bash(self) -> None:
        conn = ServerConnection(ip="1.2.3.4", local_mode=True)
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n", stderr="")
            conn.run("echo hello")
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert cmd == ["bash", "-c", "echo hello"]

    def test_local_mode_needs_sudo(self) -> None:
        conn = ServerConnection(ip="1.2.3.4", local_mode=True)
        conn.needs_sudo = True
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n", stderr="")
            conn.run("cat /etc/meridian/proxy.yml")
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert cmd == ["sudo", "-n", "bash", "-c", "cat /etc/meridian/proxy.yml"]

    def test_remote_includes_ssh_opts(self) -> None:
        conn = ServerConnection(ip="5.6.7.8", user="deploy")
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            conn.run("whoami")
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "BatchMode=yes" in cmd
            assert "ConnectTimeout=10" in cmd
            assert "StrictHostKeyChecking=yes" in cmd

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
        conn = ServerConnection(ip="1.2.3.4")
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            conn.run("echo test")
            kwargs = mock_run.call_args[1]
            assert kwargs["stdin"] == subprocess.DEVNULL

    def test_returns_command_result_with_metadata(self) -> None:
        conn = ServerConnection(ip="1.2.3.4")
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=["ssh"], returncode=0, stdout="ok\n", stderr="")
            result = conn.run("echo ok", operation_name="smoke")

        assert isinstance(result, CommandResult)
        assert result.stdout == "ok\n"
        assert result.operation_name == "smoke"
        assert result.attempts == 1
        assert result.timed_out is False

    def test_retries_until_ok_code(self) -> None:
        conn = ServerConnection(ip="1.2.3.4")
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
        conn = ServerConnection(ip="1.2.3.4", local_mode=True)
        with patch("meridian.ssh.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            conn.run("printenv FOO", cwd="/opt/app", env={"FOO": "bar baz"})

        cmd = mock_run.call_args[0][0]
        assert cmd == ["bash", "-c", "cd /opt/app && export FOO='bar baz' && printenv FOO"]

    def test_env_applies_to_compound_commands(self) -> None:
        conn = ServerConnection(ip="1.2.3.4", local_mode=True)
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
        conn = ServerConnection(ip="1.2.3.4")
        with pytest.raises(ValueError):
            conn.run("true", env={"BAD-NAME": "x"})

    def test_timeout_returns_result(self) -> None:
        conn = ServerConnection(ip="1.2.3.4")
        with patch("meridian.ssh.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=1)):
            result = conn.run("sleep 10", timeout=1)

        assert result.returncode == 124
        assert result.timed_out is True

    def test_put_text_uses_stdin_not_shell_interpolation(self) -> None:
        conn = ServerConnection(ip="1.2.3.4", user="root")
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
        conn = ServerConnection(ip="1.2.3.4", user="root")
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
            assert tcp_connect("1.2.3.4", 443) is True
            mock_conn.close.assert_called_once()

    def test_returns_false_on_connection_refused(self) -> None:
        with patch("socket.create_connection", side_effect=ConnectionRefusedError):
            assert tcp_connect("1.2.3.4", 443) is False

    def test_returns_false_on_timeout(self) -> None:
        import socket

        with patch("socket.create_connection", side_effect=socket.timeout):
            assert tcp_connect("1.2.3.4", 443) is False

    def test_returns_false_on_os_error(self) -> None:
        with patch("socket.create_connection", side_effect=OSError("Network unreachable")):
            assert tcp_connect("1.2.3.4", 443) is False

    def test_custom_timeout(self) -> None:
        mock_conn = MagicMock()
        with patch("socket.create_connection", return_value=mock_conn) as mock_create:
            tcp_connect("1.2.3.4", 80, timeout=10)
            mock_create.assert_called_once_with(("1.2.3.4", 80), timeout=10)

    def test_ipv6_address(self) -> None:
        mock_conn = MagicMock()
        with patch("socket.create_connection", return_value=mock_conn) as mock_create:
            assert tcp_connect("2001:db8::1", 443) is True
            mock_create.assert_called_once_with(("2001:db8::1", 443), timeout=5)


class TestScpHost:
    def test_ipv4_unchanged(self) -> None:
        assert scp_host("1.2.3.4") == "1.2.3.4"

    def test_ipv6_bracketed(self) -> None:
        assert scp_host("2001:db8::1") == "[2001:db8::1]"

    def test_already_bracketed_unchanged(self) -> None:
        assert scp_host("[2001:db8::1]") == "[2001:db8::1]"


class TestCheckSSH:
    def test_local_mode_skips_check(self) -> None:
        conn = ServerConnection(ip="1.2.3.4", local_mode=True)
        # Should return without doing anything
        conn.check_ssh()  # no exception

    @patch("meridian.ssh._host_key_known", return_value=True)
    def test_ssh_success(self, _mock_hk: Any) -> None:
        conn = ServerConnection(ip="1.2.3.4")
        with patch.object(conn, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n", stderr="")
            conn.check_ssh()  # should not raise

    @patch("meridian.ssh._host_key_known", return_value=True)
    def test_ssh_failure_exits(self, _mock_hk: Any) -> None:
        conn = ServerConnection(ip="1.2.3.4")
        with patch.object(conn, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=255, stdout="", stderr="Permission denied"
            )
            with pytest.raises(SSHError):
                conn.check_ssh()

    @patch("meridian.ssh._host_key_known", return_value=True)
    def test_ssh_timeout_exits(self, _mock_hk: Any) -> None:
        conn = ServerConnection(ip="1.2.3.4")
        with patch.object(conn, "run") as mock_run:
            # run() converts TimeoutExpired to returncode=124 internally
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=124, stdout="", stderr="Command timed out after 10s"
            )
            with pytest.raises(SSHError, match="timed out"):
                conn.check_ssh()

    @patch("meridian.ssh._host_key_known", return_value=True)
    def test_ssh_not_found_exits(self, _mock_hk: Any) -> None:
        conn = ServerConnection(ip="1.2.3.4")
        with patch.object(conn, "run", side_effect=FileNotFoundError):
            with pytest.raises(SSHError):
                conn.check_ssh()

    @patch("meridian.ssh._verify_host_key", return_value=False)
    @patch("meridian.ssh._host_key_known", return_value=False)
    def test_unknown_host_key_rejected_exits(self, _mock_hk: Any, _mock_vhk: Any) -> None:
        conn = ServerConnection(ip="1.2.3.4")
        with pytest.raises(SSHError):
            conn.check_ssh()

    @patch("meridian.ssh._verify_host_key", return_value=True)
    @patch("meridian.ssh._host_key_known", return_value=False)
    def test_unknown_host_key_accepted_continues(self, _mock_hk: Any, _mock_vhk: Any) -> None:
        conn = ServerConnection(ip="1.2.3.4")
        with patch.object(conn, "run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok\n", stderr="")
            conn.check_ssh()  # should not raise


class TestVerifyHostKey:
    """Tests for _verify_host_key — host key scanning, verification, and storage."""

    SCAN_OUTPUT = (
        "1.2.3.4 ssh-rsa AAAAB3NzaC1yc2EAAA...\n"
        "1.2.3.4 ecdsa-sha2-nistp256 AAAAE2VjZHNh...\n"
        "1.2.3.4 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA...\n"
    )

    def test_writes_only_preferred_key_not_all(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Only the verified (preferred) key should be written to known_hosts."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir(mode=0o700)
        known_hosts = ssh_dir / "known_hosts"
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        scan_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=self.SCAN_OUTPUT, stderr="")
        fingerprint_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="256 SHA256:abc... 1.2.3.4 (ED25519)\n", stderr=""
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
                result = _verify_host_key("1.2.3.4")

        assert result is True
        assert known_hosts.exists()
        lines = known_hosts.read_text().strip().splitlines()
        # Only 1 key written (the preferred ed25519), not all 3
        assert len(lines) == 1
        assert "ssh-ed25519" in lines[0]

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
            _verify_host_key("1.2.3.4")

        known_hosts = ssh_dir / "known_hosts"
        content = known_hosts.read_text()
        assert "ssh-ed25519" in content
        assert "ssh-rsa" not in content
        assert "ecdsa" not in content

    def test_no_tty_fails_instead_of_auto_accept(self) -> None:
        """Non-interactive mode should refuse host key, not silently accept."""
        scan_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="1.2.3.4 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA...\n", stderr=""
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
                _verify_host_key("1.2.3.4")

    def test_user_rejects_key_returns_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """User answering 'n' should return False without writing anything."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir(mode=0o700)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        scan_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="1.2.3.4 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA...\n", stderr=""
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
            result = _verify_host_key("1.2.3.4")

        assert result is False
        known_hosts = ssh_dir / "known_hosts"
        assert not known_hosts.exists()


class TestDetectLocalMode:
    """Tests for detect_local_mode — file/directory-based server detection."""

    def test_returns_true_when_proxy_yml_readable(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Root on deployed server: /etc/meridian/proxy.yml exists and is readable."""
        creds_dir = tmp_path / "meridian"
        creds_dir.mkdir()
        proxy = creds_dir / "proxy.yml"
        proxy.write_text("server:\n  ip: 198.51.100.1\n")

        monkeypatch.setattr("meridian.config.SERVER_CREDS_DIR", creds_dir)
        conn = ServerConnection(ip="198.51.100.1")
        assert conn.detect_local_mode() is True
        assert conn.local_mode is True
        assert conn.needs_sudo is False

    def test_returns_true_with_sudo_when_dir_exists_but_file_unreadable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-root on deployed server: directory exists but proxy.yml not readable."""
        creds_dir = tmp_path / "meridian"
        creds_dir.mkdir()
        proxy = creds_dir / "proxy.yml"
        proxy.write_text("server:\n  ip: 198.51.100.1\n")

        monkeypatch.setattr("meridian.config.SERVER_CREDS_DIR", creds_dir)

        # Simulate PermissionError on is_file (non-root can't stat)
        original_is_file = Path.is_file

        def fake_is_file(self: Path) -> bool:
            if self == proxy:
                raise PermissionError("Permission denied")
            return original_is_file(self)

        monkeypatch.setattr(Path, "is_file", fake_is_file)

        conn = ServerConnection(ip="198.51.100.1")
        assert conn.detect_local_mode() is True
        assert conn.local_mode is True
        assert conn.needs_sudo is True

    def test_returns_false_when_nothing_exists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Not on server (e.g. laptop, even via TUN mode): no /etc/meridian/ at all."""
        creds_dir = tmp_path / "meridian"
        # Don't create the directory — simulates a laptop
        monkeypatch.setattr("meridian.config.SERVER_CREDS_DIR", creds_dir)

        conn = ServerConnection(ip="198.51.100.1")
        assert conn.detect_local_mode() is False
        assert conn.local_mode is False
        assert conn.needs_sudo is False

    def test_returns_false_when_proxy_yml_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Edge case: proxy.yml exists but is empty (incomplete deploy)."""
        creds_dir = tmp_path / "meridian"
        creds_dir.mkdir()
        proxy = creds_dir / "proxy.yml"
        proxy.write_text("")

        monkeypatch.setattr("meridian.config.SERVER_CREDS_DIR", creds_dir)
        conn = ServerConnection(ip="198.51.100.1")
        assert conn.detect_local_mode() is False


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------


class TestRedactCommand:
    def test_redacts_secret_key(self) -> None:
        from meridian.ssh import _redact_command

        cmd = "printf 'SECRET_KEY=eyJhbGciOiJIUzI1NiJ9.abc123' > /opt/remnanode/.env"
        result = _redact_command(cmd)
        assert "eyJhbGciOiJIUzI1NiJ9" not in result
        assert "SECRET_KEY=***" in result

    def test_redacts_postgres_password(self) -> None:
        from meridian.ssh import _redact_command

        cmd = "POSTGRES_PASSWORD=supersecret123 docker compose up"
        result = _redact_command(cmd)
        assert "supersecret123" not in result
        assert "POSTGRES_PASSWORD=***" in result

    def test_redacts_token_and_database_url(self) -> None:
        from meridian.ssh import _redact_command

        cmd = "REMNAWAVE_API_TOKEN=abc123 DATABASE_URL=postgres://user:pass@db/app docker compose up"
        result = _redact_command(cmd)
        assert "abc123" not in result
        assert "postgres://user:pass@db/app" not in result
        assert "REMNAWAVE_API_TOKEN=***" in result
        assert "DATABASE_URL=***" in result

    def test_redacts_jwt_secrets(self) -> None:
        from meridian.ssh import _redact_command

        cmd = "JWT_AUTH_SECRET=abc123 JWT_API_TOKENS_SECRET=def456"
        result = _redact_command(cmd)
        assert "abc123" not in result
        assert "def456" not in result

    def test_preserves_non_secret_commands(self) -> None:
        from meridian.ssh import _redact_command

        cmd = "docker ps --format '{{.Names}}'"
        result = _redact_command(cmd)
        assert result == cmd
