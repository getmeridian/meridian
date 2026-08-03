"""Remote execution contracts for meridian-core workflows."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import Field

from meridian.core.models import CoreModel


class RemoteTarget(CoreModel):
    """Identity of the machine an executor talks to."""

    host: str
    user: str = "root"
    port: int = 22
    local: bool = False
    transport: str = "ssh"


class CommandSpec(CoreModel):
    """One shell command request for a remote executor."""

    command: str
    timeout: int = 30
    sudo: bool | None = None
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    retries: int = 1
    retry_delay: float = 0.0
    ok_codes: tuple[int, ...] = (0,)
    sensitive: bool = False
    stdin: str | None = None
    operation_name: str = ""


class RemoteCommandResult(CoreModel):
    """Transport-neutral result for remote command and file operations."""

    args: Any = Field(default_factory=list)
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
        """Whether the operation exited successfully."""
        return self.returncode == 0


class PutBytesSpec(CoreModel):
    """Write bytes to a remote path."""

    remote_path: str
    data: bytes
    mode: str | int | None = None
    owner: str | None = None
    sudo: bool | None = None
    atomic: bool = True
    create_parent: bool = False
    sensitive: bool = False
    timeout: int = 30
    operation_name: str = "write file"


class PutTextSpec(CoreModel):
    """Write text to a remote path."""

    remote_path: str
    text: str
    encoding: str = "utf-8"
    mode: str | int | None = None
    owner: str | None = None
    sudo: bool | None = None
    atomic: bool = True
    create_parent: bool = False
    sensitive: bool = False
    timeout: int = 30
    operation_name: str = "write file"


class RemoteExecutor(Protocol):
    """Transport-neutral remote execution interface used by core services."""

    @property
    def target(self) -> RemoteTarget: ...

    def run(self, spec: CommandSpec) -> RemoteCommandResult: ...

    def put_bytes(self, spec: PutBytesSpec) -> RemoteCommandResult: ...

    def put_text(self, spec: PutTextSpec) -> RemoteCommandResult: ...

    def get_text(self, remote_path: str, *, timeout: int = 30, sudo: bool | None = None) -> RemoteCommandResult: ...

    def close(self) -> None: ...


class ServerCommandResult(Protocol):
    """Lightweight command-result surface for server onboarding probes."""

    returncode: int
    stdout: str
    stderr: str


class ServerConnection(Protocol):
    """Lightweight SSH connection surface for server onboarding probes.

    Smaller than ``RemoteExecutor``; covers only the ``conn.run()`` subset
    needed by server validation, key bootstrap, and OS detection.
    """

    def run(
        self,
        command: str,
        timeout: int = 30,
        *,
        sudo: bool | None = None,
        sensitive: bool = False,
    ) -> ServerCommandResult:
        """Run a command on the server."""
