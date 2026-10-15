"""SSH connection helpers.

This module is a pure transport layer with no Rich/console dependency.
All user-facing output goes through an optional ``SSHUI`` callback
protocol so that CLI callers get Rich output while Engine/headless
callers stay dependency-free.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from meridian.config import MERIDIAN_HOME
from meridian.core.errors import MeridianError
from meridian.core.redaction import redact_string
from meridian.i18n import t
from meridian.ssh_auth import (
    _DEFAULT_UI,
    SSHUI,
    _host_key_known,
    _verify_host_key,
    ensure_askpass_script,
    ensure_multiplex_dir,
)
from meridian.ssh_transfer import _FileTransferMixin

logger = logging.getLogger("meridian.ssh")


class SSHError(MeridianError):
    """Raised when an SSH operation fails."""


class SSHPasswordOnlyError(SSHError):
    """Key login was refused by a server that would accept a password instead."""


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LOGGED_COMMAND_LIMIT = 200


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


def _stringify_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


# SSH multiplexing: reuse a single TCP connection for multiple commands
# to the same host. ControlPersist=300 keeps the master alive for 5min
# after the last command, so sequential provisioner steps don't pay
# the TCP+auth handshake each time.
#
# A Unix socket path holds at most 103 bytes on macOS, and ssh binds the master
# at the ControlPath plus a 17-character temporary suffix. Hashing the target
# keeps IPv6 addresses and long user names out of the path; a home directory
# that is still too deep turns multiplexing off instead of breaking SSH.
_MAX_CONTROL_PATH = 103 - 17

# Keepalives end a connection whose server stopped answering after about 15s,
# instead of letting every command on it hang until its own timeout.
_KEEPALIVE_OPTS = ["-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3"]


def control_path(user: str, host: str, port: int) -> Path | None:
    """The ControlMaster socket for one target, or None if the path cannot fit."""
    digest = hashlib.sha256(f"{user}@{host}:{port}".encode()).hexdigest()[:12]
    path = MERIDIAN_HOME / "ssh" / digest
    if len(os.fsencode(path)) > _MAX_CONTROL_PATH:
        return None
    return path


def multiplex_opts(user: str, host: str, port: int) -> list[str]:
    """Return ControlMaster options for one target, or none if the socket path cannot fit."""
    path = control_path(user, host, port)
    if path is None:
        return []
    escaped = str(path).replace("%", "%%")
    return ["-o", "ControlMaster=auto", "-o", f"ControlPath={escaped}", "-o", "ControlPersist=300"]


_AUTH_METHODS_RE = re.compile(r"Permission denied \(([^)]*)\)")


def offers_password_login(stderr: str) -> bool:
    """Whether a refused SSH login lists password (or keyboard-interactive) among the server's methods."""
    match = _AUTH_METHODS_RE.search(stderr)
    methods = set(match.group(1).split(",")) if match else set()
    return bool(methods & {"password", "keyboard-interactive"})


def ssh_copy_id_command(user: str, host: str, port: int = 22) -> str:
    """The manual command that installs the operator's own key on a password-only server."""
    port_flag = f" -p {port}" if port != 22 else ""
    return f"ssh-copy-id{port_flag} {shlex.quote(f'{user}@{host}')}"


def _sudo_needs_password(stderr: str) -> bool:
    """sudo -n reports both missing rights and password-only rights this way."""
    lowered = stderr.casefold()
    return "a password is required" in lowered or "a terminal is required" in lowered


def _running_unprivileged() -> bool:
    """Local commands run as this process, whatever SSH user name was resolved."""
    geteuid = getattr(os, "geteuid", None)
    return geteuid is not None and geteuid() != 0


class ServerConnection(_FileTransferMixin):
    """Manage SSH connections to a remote server.

    Non-root remote users: commands are wrapped in sudo -n sh -c via SSH.
    Non-root local users: commands run via sudo -n bash -c for privilege escalation.
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
        self.needs_sudo = local_mode and _running_unprivileged()  # on-server non-root — run commands via sudo
        self.multiplex = multiplex and not identity_file and not password
        if self.multiplex and not local_mode:
            ensure_multiplex_dir()

    def __enter__(self) -> ServerConnection:
        # Several call sites use ``with ServerConnection(...) as conn:``. The
        # SSH ControlMaster (multiplex) layer manages its own connection
        # lifetime, so __enter__/__exit__ are no-ops; defining them prevents
        # AttributeError that was silently swallowed by surrounding try/except
        # in commands/client.py.
        return self

    def __exit__(self, *_exc_info: object) -> None:
        # Multiplexed connections persist for ControlPersist's lifetime;
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
            *_KEEPALIVE_OPTS,
        ]
        if self.multiplex and not self.local_mode:
            opts.extend(multiplex_opts(self.user, self.ip, self.port))
        if self.identity_file:
            opts.extend(["-i", self.identity_file, "-o", "IdentitiesOnly=yes"])
        if self.port != 22:
            opts.extend(["-p", str(self.port)])
        return opts

    def reset_multiplex(self) -> None:
        """Drop the shared control connection so the next command dials a fresh one.

        A stalled master would otherwise hang every later command on this target
        until ControlPersist expires. ``ssh -O exit`` asks it to leave; a master too
        wedged to answer loses its socket instead, which frees the path.
        """
        if not self.multiplex or self.local_mode:
            return
        path = control_path(self.user, self.ip, self.port)
        if path is None:
            return
        escaped = str(path).replace("%", "%%")
        try:
            subprocess.run(
                ["ssh", "-o", f"ControlPath={escaped}", "-O", "exit", f"{self.user}@{self.ip}"],
                capture_output=True,
                text=True,
                timeout=5,
                stdin=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        logger.debug("SSH %s@%s: dropped the control connection after a transport failure", self.user, self.ip)

    def _after_transport(self, result: CommandResult) -> CommandResult:
        """Reset the control connection when SSH itself failed or the command timed out."""
        if not self.local_mode and (result.timed_out or result.returncode in {124, 255}):
            self.reset_multiplex()
        return result

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
        redacted = "<sensitive command>" if sensitive else redact_string(command)[:_LOGGED_COMMAND_LIMIT]
        logger.debug("SSH %s@%s: %s", self.user, self.ip, redacted)

        ok_code_set = set(ok_codes)
        last: CommandResult | None = None

        for attempt in range(1, retries + 1):
            started = time.monotonic()
            timed_out = False

            if self.local_mode:
                # On the server, privilege follows the process UID: root never needs sudo,
                # which minimal images may not even have.
                if self.needs_sudo:
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
            last = self._after_transport(result)
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
            if self.needs_sudo:
                self._require_local_sudo()
            return
        if ui is None:
            ui = _DEFAULT_UI

        target = f"{self.user}@{self.ip}" + (f":{self.port}" if self.port != 22 else "")
        ui.info(t("ssh.checking", target=target))

        # Verify host key on first connection
        if not _host_key_known(self.ip, self.port):
            if not _verify_host_key(self.ip, self.port, ui=ui):
                raise SSHError(
                    f"Host key for {self.ip} not accepted",
                    hint="Verify the fingerprint matches your VPS provider's console.",
                    category="user",
                )

        try:
            result = self.run("echo ok", timeout=10)
        except FileNotFoundError:
            raise SSHError("ssh command not found. Please install OpenSSH client.", category="system")

        if result.returncode != 0:
            stderr = result.stderr.strip()
            # run() converts TimeoutExpired to returncode=124
            if result.returncode == 124:
                raise SSHError(f"SSH connection timed out (10s) to {self.user}@{self.ip}", category="system")
            # Host key changed
            if "REMOTE HOST IDENTIFICATION HAS CHANGED" in stderr:
                ui.host_key_changed(self.ip)
                raise SSHError(f"Host key verification failed for {self.ip}", category="system")
            # sudo not found
            if self.user != "root" and ("sudo" in stderr and ("not found" in stderr or "No such file" in stderr)):
                raise SSHError(
                    f"sudo is not installed on {self.ip}",
                    hint=f"Install it as root: ssh root@{self.ip} 'apt-get install -y sudo'",
                    category="system",
                )
            if self.user != "root" and _sudo_needs_password(stderr):
                raise SSHError(
                    f"{self.user} can log in to {self.ip}, but sudo asks for a password",
                    hint=(
                        "Meridian needs root or passwordless sudo. Use --user root, or allow it on the server: "
                        f"echo '{self.user} ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/meridian"
                    ),
                    category="user",
                )
            if offers_password_login(stderr):
                manual = ssh_copy_id_command(self.user, self.ip, self.port)
                raise SSHPasswordOnlyError(
                    f"{self.user}@{self.ip} accepts only a password, and Meridian logs in with SSH keys",
                    hint=(
                        f"Install your key once with `{manual}`, then retry. In a terminal, "
                        "`meridian deploy` (without --yes) and `meridian setup` offer to set one up."
                    ),
                    category="user",
                )
            ui.ssh_failed(self.ip, self.user, stderr)
            raise SSHError(f"SSH connection failed to {self.user}@{self.ip}", category="system")
        ui.ok(t("ssh.connected"))

    def _require_local_sudo(self) -> None:
        """Fail early, with a working command, when this non-root process cannot use sudo."""
        result = subprocess.run(["sudo", "-n", "true"], capture_output=True, text=True, timeout=10, check=False)
        if result.returncode == 0:
            return
        import getpass

        user = getpass.getuser()
        raise SSHError(
            f"{user} cannot run commands as root without a password",
            hint=(
                'Run Meridian as root with your installed copy: sudo "$(command -v meridian)" <command>. '
                f"Or allow passwordless sudo: echo '{user} ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/meridian"
            ),
            category="user",
        )
