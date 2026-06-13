"""File transfer mixin for ServerConnection.

Extracted from ``ssh.py`` to keep the transport module under the 800-line
budget.  ``ServerConnection`` inherits from ``_FileTransferMixin`` defined
here; all file-oriented helpers (put_bytes, put_text, get_text, get_bytes,
write_file, fetch_credentials) live in this module.
"""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from meridian.ssh import CommandResult

logger = logging.getLogger("meridian.ssh")


class _FileTransferMixin:
    """File transfer methods extracted from ServerConnection.

    Depends on attributes provided by ServerConnection: ``run``, ``user``,
    ``ip``, ``local_mode``, ``needs_sudo``, ``_ssh_opts``,
    ``_completed_to_result``, ``_synthetic_result``.
    """

    # Declared for type-checker visibility; provided by ServerConnection.
    run: Any
    user: str
    ip: str
    local_mode: bool
    needs_sudo: bool
    _ssh_opts: list[str]
    _scp_opts: list[str]
    _scp_host: str
    _completed_to_result: Any

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
        from meridian.ssh import CommandResult

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
