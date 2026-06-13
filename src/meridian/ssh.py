"""SSH connection helpers.

This module is a pure transport layer with no Rich/console dependency.
All user-facing output goes through an optional ``SSHUI`` callback
protocol so that CLI callers get Rich output while Engine/headless
callers stay dependency-free.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from meridian.core.execution import RemoteCommandResult

from meridian.core.errors import MeridianError
from meridian.health import tcp_connect  # noqa: F401  # re-export for backward compat
from meridian.ssh_auth import (
    _DEFAULT_UI,
    SSHUI,
    _host_key_known,
    _verify_host_key,
    ensure_askpass_script,
    ensure_multiplex_dir,
    scp_host,  # noqa: F401  # re-export
)
from meridian.ssh_transfer import _FileTransferMixin

logger = logging.getLogger("meridian.ssh")


class SSHError(MeridianError):
    """Raised when an SSH operation fails.

    Attributes:
        hint: Optional recovery suggestion for the user.
        hint_type: Backward-compatible alias for ``category``.
    """

    def __init__(self, msg: str, *, hint: str = "", hint_type: str = "system") -> None:
        super().__init__(msg, hint=hint, category=hint_type)  # type: ignore[arg-type]

    @property
    def hint_type(self) -> str:
        """Backward-compatible alias for ``self.category``."""
        return self.category


# Patterns to redact from debug log output (env var assignments with secrets)
_SECRET_PATTERNS = re.compile(
    r"((?:"
    r"SECRET_KEY|PASSWORD|PASS|TOKEN|API_TOKEN|REMNAWAVE_API_TOKEN|"
    r"JWT_AUTH_SECRET|JWT_API_TOKENS_SECRET|POSTGRES_PASSWORD|METRICS_PASS|"
    r"DATABASE_URL"
    r")\s*=\s*)\S+",
    re.IGNORECASE,
)
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _redact_command(cmd: str) -> str:
    """Mask secret values in SSH commands for safe debug logging."""
    return _SECRET_PATTERNS.sub(r"\1***", cmd[:200])


@dataclass
class CommandResult:
    """Result of a command executed through ServerConnection.

    Keeps the ``subprocess.CompletedProcess`` surface used throughout the
    codebase while carrying the extra metadata needed for diagnostics.
    """

    args: Any
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    attempts: int = 1
    timed_out: bool = False
    sudo: bool = False
    redacted_command: str = ""
    operation_name: str = ""

    @property
    def ok(self) -> bool:
        """Whether the command exited successfully."""
        return self.returncode == 0

    def to_remote(self) -> RemoteCommandResult:
        """Convert to a core ``RemoteCommandResult`` with explicit field mapping."""
        from meridian.core.execution import RemoteCommandResult

        return RemoteCommandResult(
            args=self.args,
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
            duration_ms=self.duration_ms,
            attempts=self.attempts,
            timed_out=self.timed_out,
            sudo=self.sudo,
            redacted_command=self.redacted_command,
            operation_name=self.operation_name,
        )

    @classmethod
    def from_remote(cls, result: RemoteCommandResult) -> CommandResult:
        """Create from a core ``RemoteCommandResult`` with explicit field mapping."""
        return cls(
            args=result.args,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=result.duration_ms,
            attempts=result.attempts,
            timed_out=result.timed_out,
            sudo=result.sudo,
            redacted_command=result.redacted_command,
            operation_name=result.operation_name,
        )

    def check_returncode(self) -> None:
        if self.returncode != 0:
            raise subprocess.CalledProcessError(
                self.returncode,
                self.args,
                output=self.stdout,
                stderr=self.stderr,
            )


def _stringify_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


SSH_OPTS: list[str] = [
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=10",
    "-o",
    "StrictHostKeyChecking=yes",
]

# SSH multiplexing: reuse a single TCP connection for multiple commands
# to the same host. ControlPersist=300 keeps the master alive for 5min
# after the last command, so sequential provisioner steps don't pay
# the TCP+auth handshake each time.
SSH_MULTIPLEX_OPTS: list[str] = [
    "-o",
    "ControlMaster=auto",
    "-o",
    "ControlPath=~/.meridian/ssh/%r@%h:%p",
    "-o",
    "ControlPersist=300",
]


class ServerConnection(_FileTransferMixin):
    """Manage SSH connections to a remote server.

    Non-root remote users: commands are wrapped in sudo -n sh -c via SSH.
    Non-root local users: detect_local_mode sets needs_sudo, commands run
    via sudo -n bash -c for privilege escalation.
    Passwordless sudo is required (standard on AWS/GCP/Azure/DO).
    """

    def __init__(
        self,
        ip: str,
        user: str = "root",
        local_mode: bool = False,
        port: int = 22,
        multiplex: bool = True,
        identity_file: str = "",
        password: str = "",
    ) -> None:
        self.ip = ip
        self.user = user
        self.port = port
        self.local_mode = local_mode
        self.identity_file = identity_file
        self.password = password
        self.needs_sudo = False  # on-server non-root — run commands via sudo
        self.multiplex = multiplex and not identity_file and not password
        if self.multiplex and not local_mode:
            ensure_multiplex_dir()

    def __enter__(self) -> ServerConnection:
        # Several call sites use ``with ServerConnection(...) as conn:``. The
        # SSH ControlMaster (multiplex) layer manages its own connection
        # lifetime, so __enter__/__exit__ are no-ops; defining them prevents
        # AttributeError that was silently swallowed by surrounding try/except
        # in commands/client.py and commands/recover.py.
        return self

    def __exit__(self, *exc_info: object) -> None:
        # Multiplexed connections persist for SSH_MULTIPLEX_OPTS' lifetime;
        # nothing to release here.
        return None

    @property
    def _ssh_opts(self) -> list[str]:
        opts = [
            "-o",
            "BatchMode=no" if self.password else "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=yes",
        ]
        if self.multiplex and not self.local_mode:
            opts.extend(SSH_MULTIPLEX_OPTS)
        if self.identity_file:
            opts.extend(["-i", self.identity_file, "-o", "IdentitiesOnly=yes"])
        if self.port != 22:
            opts.extend(["-p", str(self.port)])
        return opts

    @property
    def _scp_opts(self) -> list[str]:
        """SSH options for SCP commands (uses -P for port, not -p)."""
        opts = [
            "-o",
            "BatchMode=no" if self.password else "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "StrictHostKeyChecking=yes",
        ]
        if self.multiplex and not self.local_mode:
            opts.extend(SSH_MULTIPLEX_OPTS)
        if self.identity_file:
            opts.extend(["-i", self.identity_file, "-o", "IdentitiesOnly=yes"])
        if self.port != 22:
            opts.extend(["-P", str(self.port)])
        return opts

    @property
    def _scp_host(self) -> str:
        """Host string for SCP commands (brackets IPv6 addresses)."""
        if ":" in self.ip and not self.ip.startswith("["):
            return f"[{self.ip}]"
        return self.ip

    def _prepare_command(
        self,
        command: str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        """Apply cwd/env wrappers to a shell command."""
        parts: list[str] = []
        if cwd:
            parts.append(f"cd {shlex.quote(cwd)}")
        if env:
            assignments = []
            for key, value in env.items():
                if not _ENV_KEY_RE.match(key):
                    raise ValueError(f"Invalid environment variable name: {key!r}")
                assignments.append(f"{key}={shlex.quote(str(value))}")
            parts.append("export " + " ".join(assignments))
        parts.append(command)
        return " && ".join(parts)

    def _completed_to_result(
        self,
        completed: subprocess.CompletedProcess[Any],
        *,
        duration_ms: int,
        attempts: int,
        timed_out: bool,
        sudo: bool,
        redacted_command: str,
        operation_name: str,
    ) -> CommandResult:
        return CommandResult(
            args=completed.args,
            returncode=completed.returncode,
            stdout=_stringify_output(completed.stdout),
            stderr=_stringify_output(completed.stderr),
            duration_ms=duration_ms,
            attempts=attempts,
            timed_out=timed_out,
            sudo=sudo,
            redacted_command=redacted_command,
            operation_name=operation_name,
        )

    def run(
        self,
        command: str,
        timeout: int = 30,
        *,
        sudo: bool | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        retries: int = 1,
        retry_delay: float = 0.0,
        ok_codes: Iterable[int] = (0,),
        sensitive: bool = False,
        input: str | None = None,
        operation_name: str = "",
    ) -> CommandResult:
        """Run a command on the remote server via SSH.

        Args:
            command: Shell command to execute.
            timeout: Timeout in seconds.
            sudo: Force sudo wrapping. None = auto (sudo when user != root).
            cwd: Optional working directory on the target.
            env: Environment variables to prefix before the command.
            retries: Number of attempts before returning the last result.
            retry_delay: Seconds to sleep between failed attempts.
            ok_codes: Return codes that count as success for retry purposes.
            sensitive: Hide command content from logs/result metadata.
            input: Optional stdin text for the process.
            operation_name: Human-readable operation label for diagnostics.

        Returns a CommandResult with returncode=124 if the command times out
        (matching GNU ``timeout`` convention), instead of letting
        ``subprocess.TimeoutExpired`` crash the caller.
        """
        if retries < 1:
            raise ValueError("retries must be >= 1")

        command = self._prepare_command(command, cwd=cwd, env=env)
        use_sudo = sudo if sudo is not None else (self.user != "root")
        redacted = "<sensitive command>" if sensitive else _redact_command(command)
        logger.debug("SSH %s@%s: %s", self.user, self.ip, redacted)

        ok_code_set = set(ok_codes)
        last: CommandResult | None = None

        for attempt in range(1, retries + 1):
            started = time.monotonic()
            timed_out = False

            if self.local_mode:
                if self.needs_sudo or use_sudo:
                    cmd = ["sudo", "-n", "bash", "-c", command]
                else:
                    cmd = ["bash", "-c", command]
                try:
                    completed = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        stdin=subprocess.DEVNULL if input is None else None,
                        input=input,
                    )
                except subprocess.TimeoutExpired:
                    timed_out = True
                    completed = subprocess.CompletedProcess(
                        args=cmd,
                        returncode=124,
                        stdout="",
                        stderr=f"Command timed out after {timeout}s",
                    )
            else:
                remote_command = command
                if use_sudo and not self.needs_sudo:
                    # Non-root remote user: wrap in sudo via SSH. SSH passes
                    # the command string to the remote shell, which handles the
                    # first layer of quoting. sudo -n sh -c adds a second layer.
                    remote_command = f"sudo -n sh -c {shlex.quote(remote_command)}"
                cmd = ["ssh", *self._ssh_opts, f"{self.user}@{self.ip}", remote_command]
                process_env = None
                if self.password:
                    process_env = os.environ.copy()
                    process_env.update(
                        {
                            "DISPLAY": process_env.get("DISPLAY") or "meridian",
                            "MERIDIAN_SSH_PASSWORD": self.password,
                            "SSH_ASKPASS": str(ensure_askpass_script()),
                            "SSH_ASKPASS_REQUIRE": "force",
                        }
                    )
                try:
                    completed = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        stdin=subprocess.DEVNULL if input is None else None,
                        input=input,
                        env=process_env,
                    )
                except subprocess.TimeoutExpired:
                    timed_out = True
                    completed = subprocess.CompletedProcess(
                        args=cmd,
                        returncode=124,
                        stdout="",
                        stderr=f"Command timed out after {timeout}s",
                    )

            duration_ms = int((time.monotonic() - started) * 1000)
            result = self._completed_to_result(
                completed,
                duration_ms=duration_ms,
                attempts=attempt,
                timed_out=timed_out,
                sudo=bool(self.needs_sudo or use_sudo),
                redacted_command=redacted,
                operation_name=operation_name,
            )
            logger.debug("SSH rc=%d attempt=%d/%d", result.returncode, attempt, retries)
            last = result
            if result.returncode in ok_code_set:
                return result
            if attempt < retries and retry_delay > 0:
                time.sleep(retry_delay)

        assert last is not None
        return last

    def check_ssh(self, *, ui: SSHUI | None = None) -> None:
        """Verify SSH connectivity. Raises SSHError on failure.

        On first connection to an unknown host, scans the host key,
        displays the fingerprint, and prompts the user to verify it.

        Args:
            ui: Optional UI callback for progress/error output.
                Defaults to logger-based output.
        """
        if self.local_mode:
            return
        if ui is None:
            ui = _DEFAULT_UI

        ui.info(f"Checking SSH connectivity to {self.user}@{self.ip}" + (f":{self.port}" if self.port != 22 else ""))

        # Verify host key on first connection
        if not _host_key_known(self.ip, self.port):
            if not _verify_host_key(self.ip, self.port, ui=ui):
                raise SSHError(
                    f"Host key for {self.ip} not accepted",
                    hint="Verify the fingerprint matches your VPS provider's console.",
                    hint_type="user",
                )

        try:
            result = self.run("echo ok", timeout=10)
        except FileNotFoundError:
            raise SSHError("ssh command not found. Please install OpenSSH client.", hint_type="system")

        if result.returncode != 0:
            stderr = result.stderr.strip()
            # run() converts TimeoutExpired to returncode=124
            if result.returncode == 124:
                raise SSHError(f"SSH connection timed out (10s) to {self.user}@{self.ip}", hint_type="system")
            # Host key changed
            if "REMOTE HOST IDENTIFICATION HAS CHANGED" in stderr:
                ui.host_key_changed(self.ip)
                raise SSHError(f"Host key verification failed for {self.ip}", hint_type="system")
            # sudo not found
            if self.user != "root" and ("sudo" in stderr and ("not found" in stderr or "No such file" in stderr)):
                raise SSHError(
                    f"sudo is not installed on {self.ip}",
                    hint=f"Install it as root: ssh root@{self.ip} 'apt-get install -y sudo'",
                    hint_type="system",
                )
            ui.ssh_failed(self.ip, self.user, stderr)
            raise SSHError(f"SSH connection failed to {self.user}@{self.ip}", hint_type="system")
        ui.ok("SSH connection successful")

    def detect_local_mode(self) -> bool:
        """Check if we're running on the target server itself.

        Detection is file-based only: /etc/meridian/node.yml (v4) or
        /etc/meridian/proxy.yml (v3 compat) readable (root), or
        /etc/meridian/ directory exists but files not readable (non-root).

        Does NOT use public IP matching — that produces false positives when
        the user is connected to the server via TUN mode (VPN), since their
        outbound IP matches the server IP.
        """
        from meridian.config import SERVER_CREDS_DIR, SERVER_NODE_CONFIG

        file_check_failed = False

        # v4: node.yml
        try:
            if SERVER_NODE_CONFIG.is_file() and SERVER_NODE_CONFIG.stat().st_size > 0:
                self.local_mode = True
                return True
        except (PermissionError, OSError):
            file_check_failed = True

        # v3 compat: proxy.yml
        proxy = SERVER_CREDS_DIR / "proxy.yml"
        try:
            if proxy.is_file() and proxy.stat().st_size > 0:
                self.local_mode = True
                return True
        except (PermissionError, OSError):
            file_check_failed = True

        # Dir exists but files not readable → non-root on deployed server
        if file_check_failed:
            try:
                if SERVER_CREDS_DIR.is_dir():
                    logger.warning("Running as non-root on the server. Using sudo for commands.")
                    self.local_mode = True
                    self.needs_sudo = True
                    return True
            except (PermissionError, OSError):
                pass

        return False
