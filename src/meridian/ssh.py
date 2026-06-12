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
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

from meridian.ssh_keys import host_key_known

logger = logging.getLogger("meridian.ssh")


class SSHError(Exception):
    """Raised when an SSH operation fails.

    Attributes:
        hint: Optional recovery suggestion for the user.
        hint_type: Error category --- "user", "system", or "bug".
    """

    def __init__(self, msg: str, *, hint: str = "", hint_type: str = "system") -> None:
        super().__init__(msg)
        self.hint = hint
        self.hint_type = hint_type


# ---------------------------------------------------------------------------
# UI callback protocol --- decouples transport from presentation
# ---------------------------------------------------------------------------


class SSHUI(Protocol):
    """Callback interface for SSH user-facing output.

    The default implementation logs via the ``meridian.ssh`` logger.
    Pass a Rich-based implementation from the CLI layer to get
    coloured terminal output identical to the previous behaviour.
    """

    def info(self, msg: str) -> None: ...
    def ok(self, msg: str) -> None: ...
    def warn(self, msg: str) -> None: ...
    def host_key_prompt(self, ip: str, fingerprint: str, algo: str) -> bool:
        """Display host-key fingerprint and ask the user to accept.

        Returns ``True`` if the user accepts the key, ``False`` otherwise.
        Raises ``SSHError`` if no interactive terminal is available.
        """
        ...

    def ssh_failed(self, ip: str, user: str, stderr: str) -> None: ...
    def host_key_changed(self, ip: str) -> None: ...


class _LogUI:
    """Default SSHUI that writes to the ``meridian.ssh`` logger."""

    def info(self, msg: str) -> None:
        logger.info(msg)

    def ok(self, msg: str) -> None:
        logger.info(msg)

    def warn(self, msg: str) -> None:
        logger.warning(msg)

    def host_key_prompt(self, ip: str, fingerprint: str, algo: str) -> bool:
        """Prompt via /dev/tty (OS-level, no Rich dependency)."""
        lines = [f"First connection to {ip}"]
        if fingerprint:
            lines.append(f"Host key fingerprint: {fingerprint}")
        else:
            lines.append(f"Host key type: {algo}")
        lines.append("Verify this matches your VPS provider's console.")
        lines.append("A mismatch may indicate a network attack.")
        for ln in lines:
            logger.info(ln)

        try:
            with open("/dev/tty") as tty:
                print("\n  -> Trust this host key? [Y/n] ", end="", flush=True)  # noqa: T201
                answer = tty.readline().strip().lower()
        except OSError:
            raise SSHError(
                f"Cannot verify host key for {ip} (no terminal available)",
                hint="Run interactively, or pre-add the key: ssh-keyscan IP >> ~/.ssh/known_hosts",
                hint_type="user",
            )
        return answer in ("", "y", "yes")

    def ssh_failed(self, ip: str, user: str, stderr: str) -> None:
        logger.error("SSH connection failed: %s", stderr)
        logger.info("1. Copy your SSH key:  ssh-copy-id %s@%s", user, ip)
        logger.info("2. Test manually:      ssh %s@%s", user, ip)
        logger.info("3. Different user:     meridian deploy IP --user ubuntu")

    def host_key_changed(self, ip: str) -> None:
        logger.error("Host key for %s has CHANGED!", ip)
        logger.warning("This could indicate a network attack (MitM).")
        logger.info("If you recently rebuilt this server, remove the old key:")
        logger.info("  ssh-keygen -R %s", ip)


_DEFAULT_UI = _LogUI()

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


def ensure_multiplex_dir() -> None:
    """Create the SSH control socket directory if it doesn't exist."""
    sock_dir = Path.home() / ".meridian" / "ssh"
    sock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)


def ensure_askpass_script() -> Path:
    """Create the local askpass helper used for short-lived password SSH."""
    script = Path.home() / ".meridian" / "ssh" / "askpass.sh"
    script.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    script.write_text("#!/bin/sh\nprintf '%s\\n' \"$MERIDIAN_SSH_PASSWORD\"\n", encoding="utf-8")
    script.chmod(0o700)
    return script


def scp_host(ip: str) -> str:
    """Format IP for SCP host:path syntax (brackets IPv6)."""
    if ":" in ip and not ip.startswith("["):
        return f"[{ip}]"
    return ip


def _host_key_known(ip: str, port: int = 22) -> bool:
    """Thin wrapper so test patches on ``meridian.ssh._host_key_known`` keep working."""
    return host_key_known(ip, port)


def _verify_host_key(ip: str, port: int = 22, *, ui: SSHUI | None = None) -> bool:
    """Scan, display, and prompt user to verify the SSH host key.

    Returns True if the user accepts (key added to known_hosts), False otherwise.
    Uses ssh-keyscan to fetch the key and ssh-keygen to compute the fingerprint.
    """
    if ui is None:
        ui = _DEFAULT_UI

    # Scan the host key
    keyscan_cmd = ["ssh-keyscan", "-T", "5"]
    if port != 22:
        keyscan_cmd.extend(["-p", str(port)])
    keyscan_cmd.append(ip)
    try:
        result = subprocess.run(
            keyscan_cmd,
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0 or not result.stdout.strip():
            ui.warn(f"Could not scan host key for {ip}")
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError):
        ui.warn(f"Could not scan host key for {ip}")
        return False

    key_lines = [line for line in result.stdout.strip().splitlines() if line and not line.startswith("#")]
    if not key_lines:
        ui.warn(f"No host keys found for {ip}")
        return False

    # Prefer ed25519 > ecdsa > rsa
    preferred = None
    for pref in ("ssh-ed25519", "ecdsa-sha2", "ssh-rsa"):
        for line in key_lines:
            if pref in line:
                preferred = line
                break
        if preferred:
            break
    if not preferred:
        preferred = key_lines[0]

    # Compute fingerprint
    try:
        result = subprocess.run(
            ["ssh-keygen", "-lf", "-"],
            input=preferred,
            capture_output=True,
            text=True,
            timeout=5,
        )
        fingerprint = result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        fingerprint = ""

    # Extract algorithm for display
    key_type = preferred.split()[1] if len(preferred.split()) >= 2 else "unknown"
    parts = preferred.split()
    algo = parts[1] if len(parts) >= 3 else key_type

    # Prompt user via UI callback (handles display + TTY interaction)
    accepted = ui.host_key_prompt(ip, fingerprint, algo)
    if not accepted:
        return False

    # Add only the verified key to known_hosts (user only saw this fingerprint)
    known_hosts = Path.home() / ".ssh" / "known_hosts"
    known_hosts.parent.mkdir(mode=0o700, exist_ok=True)
    with open(known_hosts, "a") as f:
        f.write(preferred + "\n")

    ui.ok("Host key saved")
    return True


class ServerConnection:
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

    def fetch_credentials(self, local_creds_dir: Path) -> bool:
        """Fetch credentials from server's /etc/meridian/ via SCP.

        In local mode (root), copies directly from /etc/meridian/.
        In remote mode, uses SCP for root or SSH+sudo for non-root users
        (SCP can't read root-owned /etc/meridian/ without sudo).
        """
        if self.local_mode:
            return self._copy_local_credentials(local_creds_dir)

        local_creds_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        if self.user != "root":
            # Non-root: SCP can't read root-owned /etc/meridian/.
            # Use SSH + sudo cat instead (conn.run() adds sudo automatically).
            result = self.run("cat /etc/meridian/proxy.yml", timeout=30)
            if result.returncode == 0 and result.stdout:
                dst = local_creds_dir / "proxy.yml"
                dst.write_text(result.stdout, encoding="utf-8")
                dst.chmod(0o600)
                return True
            return False

        try:
            scp_result = subprocess.run(
                [
                    "scp",
                    *self._scp_opts,
                    f"{self.user}@{self._scp_host}:/etc/meridian/proxy.yml",
                    str(local_creds_dir / "proxy.yml"),
                ],
                capture_output=True,
                text=True,
                timeout=30,
                stdin=subprocess.DEVNULL,
            )
            if scp_result.returncode == 0:
                (local_creds_dir / "proxy.yml").chmod(0o600)
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return False

    def _synthetic_result(
        self,
        *,
        args: Any,
        returncode: int,
        stdout: str = "",
        stderr: str = "",
        started: float | None = None,
        timed_out: bool = False,
        sudo: bool = False,
        redacted_command: str = "",
        operation_name: str = "",
    ) -> CommandResult:
        duration_ms = int((time.monotonic() - started) * 1000) if started is not None else 0
        return CommandResult(
            args=args,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            timed_out=timed_out,
            sudo=sudo,
            redacted_command=redacted_command,
            operation_name=operation_name,
        )

    def _write_bytes_direct(
        self,
        path: str,
        data: bytes,
        *,
        sudo: bool,
        timeout: int,
        sensitive: bool,
        operation_name: str,
    ) -> CommandResult:
        """Write bytes to one target path without chmod/chown/mv."""
        q_path = shlex.quote(path)
        started = time.monotonic()
        if sensitive:
            redacted = f"write {len(data)} bytes to <sensitive path>"
        else:
            redacted = f"write {len(data)} bytes to {q_path}"
        logger.debug("SSH %s@%s: %s", self.user, self.ip, redacted)

        if self.local_mode:
            if sudo or self.needs_sudo or self.user != "root":
                cmd = ["sudo", "-n", "tee", path]
                try:
                    completed = subprocess.run(
                        cmd,
                        input=data,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        timeout=timeout,
                    )
                    return self._completed_to_result(
                        completed,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        attempts=1,
                        timed_out=False,
                        sudo=True,
                        redacted_command=redacted,
                        operation_name=operation_name,
                    )
                except subprocess.TimeoutExpired:
                    return self._synthetic_result(
                        args=cmd,
                        returncode=124,
                        stderr=f"Command timed out after {timeout}s",
                        started=started,
                        timed_out=True,
                        sudo=True,
                        redacted_command=redacted,
                        operation_name=operation_name,
                    )
                except (FileNotFoundError, OSError) as e:
                    return self._synthetic_result(
                        args=cmd,
                        returncode=1,
                        stderr=str(e),
                        started=started,
                        sudo=True,
                        redacted_command=redacted,
                        operation_name=operation_name,
                    )
            try:
                Path(path).write_bytes(data)
                return self._synthetic_result(
                    args=["write", path],
                    returncode=0,
                    started=started,
                    redacted_command=redacted,
                    operation_name=operation_name,
                )
            except OSError as e:
                return self._synthetic_result(
                    args=["write", path],
                    returncode=1,
                    stderr=str(e),
                    started=started,
                    redacted_command=redacted,
                    operation_name=operation_name,
                )

        remote_cmd = f"cat > {q_path}"
        if sudo or self.user != "root":
            remote_cmd = f"sudo -n tee {q_path} > /dev/null"
        cmd = ["ssh", *self._ssh_opts, f"{self.user}@{self.ip}", remote_cmd]
        try:
            completed = subprocess.run(
                cmd,
                input=data,
                capture_output=True,
                timeout=timeout,
            )
            return self._completed_to_result(
                completed,
                duration_ms=int((time.monotonic() - started) * 1000),
                attempts=1,
                timed_out=False,
                sudo=bool(sudo or self.user != "root"),
                redacted_command=redacted,
                operation_name=operation_name,
            )
        except subprocess.TimeoutExpired:
            return self._synthetic_result(
                args=cmd,
                returncode=124,
                stderr=f"Command timed out after {timeout}s",
                started=started,
                timed_out=True,
                sudo=bool(sudo or self.user != "root"),
                redacted_command=redacted,
                operation_name=operation_name,
            )
        except (FileNotFoundError, OSError) as e:
            return self._synthetic_result(
                args=cmd,
                returncode=1,
                stderr=str(e),
                started=started,
                sudo=bool(sudo or self.user != "root"),
                redacted_command=redacted,
                operation_name=operation_name,
            )

    def put_bytes(
        self,
        remote_path: str,
        data: bytes,
        *,
        mode: str | int | None = None,
        owner: str | None = None,
        sudo: bool | None = None,
        atomic: bool = True,
        create_parent: bool = False,
        sensitive: bool = False,
        timeout: int = 30,
        operation_name: str = "write file",
    ) -> CommandResult:
        """Write bytes to the target, optionally atomically and with metadata.

        Content is passed on stdin, never interpolated into the shell command.
        """
        use_sudo = sudo if sudo is not None else (self.user != "root" or self.needs_sudo)
        q_remote = shlex.quote(remote_path)
        parent = str(Path(remote_path).parent)
        if create_parent and parent and parent != ".":
            mkdir = self.run(
                f"mkdir -p {shlex.quote(parent)}",
                timeout=timeout,
                sudo=use_sudo,
                sensitive=sensitive,
                operation_name=f"{operation_name}: mkdir",
            )
            if mkdir.returncode != 0:
                return mkdir

        write_path = remote_path
        if atomic:
            write_path = f"{remote_path}.tmp.{int(time.time() * 1000)}"

        mode_s = f"{mode:o}" if isinstance(mode, int) else str(mode) if mode is not None else ""
        precreate_mode = "600" if sensitive else mode_s
        if precreate_mode:
            q_write = shlex.quote(write_path)
            create = self.run(
                f"install -m {shlex.quote(precreate_mode)} /dev/null {q_write}",
                timeout=timeout,
                sudo=use_sudo,
                sensitive=sensitive,
                operation_name=f"{operation_name}: create target",
            )
            if create.returncode != 0:
                return create

        result = self._write_bytes_direct(
            write_path,
            data,
            sudo=use_sudo,
            timeout=timeout,
            sensitive=sensitive,
            operation_name=operation_name,
        )
        if result.returncode != 0:
            return result

        q_write = shlex.quote(write_path)
        if mode is not None:
            chmod = self.run(
                f"chmod {shlex.quote(mode_s)} {q_write}",
                timeout=timeout,
                sudo=use_sudo,
                sensitive=sensitive,
                operation_name=f"{operation_name}: chmod",
            )
            if chmod.returncode != 0:
                return chmod

        if owner:
            chown = self.run(
                f"chown {shlex.quote(owner)} {q_write}",
                timeout=timeout,
                sudo=use_sudo,
                sensitive=sensitive,
                operation_name=f"{operation_name}: chown",
            )
            if chown.returncode != 0:
                return chown

        if atomic:
            mv = self.run(
                f"mv {q_write} {q_remote}",
                timeout=timeout,
                sudo=use_sudo,
                sensitive=sensitive,
                operation_name=f"{operation_name}: move",
            )
            if mv.returncode != 0:
                self.run(f"rm -f {q_write}", timeout=5, sudo=use_sudo, sensitive=True)
                return mv
            return mv

        return result

    def put_text(
        self,
        remote_path: str,
        text: str,
        *,
        encoding: str = "utf-8",
        **kwargs: Any,
    ) -> CommandResult:
        """Write text to the target using ``put_bytes``."""
        return self.put_bytes(remote_path, text.encode(encoding), **kwargs)

    def get_text(self, remote_path: str, *, timeout: int = 30, sudo: bool | None = None) -> CommandResult:
        """Read a remote text file through the normal command path."""
        return self.run(f"cat {shlex.quote(remote_path)}", timeout=timeout, sudo=sudo, sensitive=True)

    def get_bytes(self, remote_path: str, *, timeout: int = 30, sudo: bool | None = None) -> bytes:
        """Read a remote file as bytes.

        This is intentionally small: binary reads are currently needed only for
        support utilities, while text reads should use ``get_text`` so callers
        retain return-code and stderr details.
        """
        result = self.get_text(remote_path, timeout=timeout, sudo=sudo)
        if result.returncode != 0:
            return b""
        return result.stdout.encode()

    def write_file(self, local_path: Path, remote_path: str) -> bool:
        """Backward-compatible wrapper around ``put_bytes``."""
        if self.local_mode and Path(remote_path) == local_path:
            return True
        result = self.put_bytes(
            remote_path,
            local_path.read_bytes(),
            mode="600",
            atomic=False,
            sensitive=True,
            operation_name="write local file",
        )
        return result.returncode == 0

    def _copy_local_credentials(self, local_creds_dir: Path) -> bool:
        """Copy credentials from /etc/meridian/ in local mode.

        When needs_sudo is set, uses sudo to read root-owned credential files.
        """
        from meridian.config import SERVER_CREDS_DIR

        src = SERVER_CREDS_DIR / "proxy.yml"
        dst = local_creds_dir / "proxy.yml"

        if dst == src:
            return True

        local_creds_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        # Copy main credentials (required)
        if not self._copy_one_file(SERVER_CREDS_DIR / "proxy.yml", local_creds_dir / "proxy.yml"):
            return False
        return True

    def _copy_one_file(self, src: Path, dst: Path) -> bool:
        """Copy a single file, using sudo if needed. Returns True on success."""
        if self.needs_sudo:
            try:
                result = subprocess.run(
                    ["sudo", "-n", "cat", str(src)],
                    capture_output=True,
                    timeout=5,
                    stdin=subprocess.DEVNULL,
                )
                if result.returncode != 0:
                    return False
                dst.write_bytes(result.stdout)
                dst.chmod(0o600)
                return True
            except (subprocess.TimeoutExpired, FileNotFoundError):
                return False
        try:
            if src.is_file():
                shutil.copy2(str(src), str(dst))
                dst.chmod(0o600)
                return True
        except (PermissionError, OSError):
            pass
        return False


def tcp_connect(host: str, port: int, timeout: int = 5) -> bool:
    """Test TCP connectivity to host:port using a Python socket."""
    import socket as _socket

    try:
        conn = _socket.create_connection((host, port), timeout=timeout)
        conn.close()
        return True
    except (OSError, _socket.timeout):
        return False
