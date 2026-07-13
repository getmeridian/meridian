"""Adapters between core remote executors and provisioning connections."""

from __future__ import annotations

from collections.abc import Iterable

from meridian.core.execution import CommandSpec, PutBytesSpec, PutTextSpec, RemoteCommandResult, RemoteExecutor
from meridian.ssh import CommandResult


def _connection_result(result: RemoteCommandResult) -> CommandResult:
    """Convert a core command result into the provisioning result type."""
    return CommandResult.from_remote(result)


class RemoteExecutorConnection:
    """ServerConnection-shaped facade backed by a core RemoteExecutor.

    Provision steps use ``conn.run(...)`` while orchestration depends on
    executor contracts implemented by SSH, local, or daemon transports.
    """

    def __init__(self, executor: RemoteExecutor) -> None:
        self.executor = executor
        self.ip = executor.target.host
        self.user = executor.target.user
        self.port = executor.target.port
        self.local_mode = executor.target.local
        self.needs_sudo = False

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
        return _connection_result(
            self.executor.run(
                CommandSpec(
                    command=command,
                    timeout=timeout,
                    sudo=sudo,
                    cwd=cwd,
                    env=env or {},
                    retries=retries,
                    retry_delay=retry_delay,
                    ok_codes=tuple(ok_codes),
                    sensitive=sensitive,
                    stdin=input,
                    operation_name=operation_name,
                )
            )
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
        return _connection_result(
            self.executor.put_bytes(
                PutBytesSpec(
                    remote_path=remote_path,
                    data=data,
                    mode=mode,
                    owner=owner,
                    sudo=sudo,
                    atomic=atomic,
                    create_parent=create_parent,
                    sensitive=sensitive,
                    timeout=timeout,
                    operation_name=operation_name,
                )
            )
        )

    def put_text(
        self,
        remote_path: str,
        text: str,
        *,
        encoding: str = "utf-8",
        mode: str | int | None = None,
        owner: str | None = None,
        sudo: bool | None = None,
        atomic: bool = True,
        create_parent: bool = False,
        sensitive: bool = False,
        timeout: int = 30,
        operation_name: str = "write file",
    ) -> CommandResult:
        return _connection_result(
            self.executor.put_text(
                PutTextSpec(
                    remote_path=remote_path,
                    text=text,
                    encoding=encoding,
                    mode=mode,
                    owner=owner,
                    sudo=sudo,
                    atomic=atomic,
                    create_parent=create_parent,
                    sensitive=sensitive,
                    timeout=timeout,
                    operation_name=operation_name,
                )
            )
        )

    def get_text(self, remote_path: str, *, timeout: int = 30, sudo: bool | None = None) -> CommandResult:
        return _connection_result(self.executor.get_text(remote_path, timeout=timeout, sudo=sudo))

    def get_bytes(self, remote_path: str, *, timeout: int = 30, sudo: bool | None = None) -> bytes:
        result = self.get_text(remote_path, timeout=timeout, sudo=sudo)
        if result.returncode != 0:
            return b""
        return result.stdout.encode()

    def close(self) -> None:
        self.executor.close()
