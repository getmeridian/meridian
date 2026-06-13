"""SSH adapter for meridian-core remote execution contracts."""

from __future__ import annotations

from typing import Any

from meridian.core.execution import (
    CommandSpec,
    PutBytesSpec,
    PutTextSpec,
    RemoteCommandResult,
    RemoteTarget,
)
from meridian.ssh import CommandResult, ServerConnection


def remote_command_result(result: CommandResult) -> RemoteCommandResult:
    """Convert the current SSH command result into a core result."""
    return result.to_remote()


class SSHRemoteExecutor:
    """RemoteExecutor implementation backed by the existing ServerConnection."""

    def __init__(self, connection: ServerConnection) -> None:
        self.connection = connection

    @classmethod
    def connect(
        cls,
        host: str,
        *,
        user: str = "root",
        local_mode: bool = False,
        port: int = 22,
        multiplex: bool = True,
    ) -> SSHRemoteExecutor:
        """Create an executor from connection parameters."""
        return cls(ServerConnection(host, user=user, local_mode=local_mode, port=port, multiplex=multiplex))

    @property
    def target(self) -> RemoteTarget:
        return RemoteTarget(
            host=self.connection.ip,
            user=self.connection.user,
            port=self.connection.port,
            local=self.connection.local_mode,
            transport="ssh",
        )

    def as_server_connection(self) -> ServerConnection:
        """Return the wrapped connection for migration-period call sites."""
        return self.connection

    def run(self, spec: CommandSpec) -> RemoteCommandResult:
        return remote_command_result(
            self.connection.run(
                spec.command,
                timeout=spec.timeout,
                sudo=spec.sudo,
                cwd=spec.cwd,
                env=dict(spec.env),
                retries=spec.retries,
                retry_delay=spec.retry_delay,
                ok_codes=spec.ok_codes,
                sensitive=spec.sensitive,
                input=spec.stdin,
                operation_name=spec.operation_name,
            )
        )

    def put_bytes(self, spec: PutBytesSpec) -> RemoteCommandResult:
        return remote_command_result(
            self.connection.put_bytes(
                spec.remote_path,
                spec.data,
                mode=spec.mode,
                owner=spec.owner,
                sudo=spec.sudo,
                atomic=spec.atomic,
                create_parent=spec.create_parent,
                sensitive=spec.sensitive,
                timeout=spec.timeout,
                operation_name=spec.operation_name,
            )
        )

    def put_text(self, spec: PutTextSpec) -> RemoteCommandResult:
        return remote_command_result(
            self.connection.put_text(
                spec.remote_path,
                spec.text,
                encoding=spec.encoding,
                mode=spec.mode,
                owner=spec.owner,
                sudo=spec.sudo,
                atomic=spec.atomic,
                create_parent=spec.create_parent,
                sensitive=spec.sensitive,
                timeout=spec.timeout,
                operation_name=spec.operation_name,
            )
        )

    def get_text(self, remote_path: str, *, timeout: int = 30, sudo: bool | None = None) -> RemoteCommandResult:
        return remote_command_result(self.connection.get_text(remote_path, timeout=timeout, sudo=sudo))

    def close(self) -> None:
        close = getattr(self.connection, "close", None)
        if callable(close):
            close()


def ssh_remote_executor(connection: ServerConnection | Any) -> SSHRemoteExecutor:
    """Wrap a ServerConnection-compatible object as an SSH executor."""
    return SSHRemoteExecutor(connection)
