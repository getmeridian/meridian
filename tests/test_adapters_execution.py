"""Tests for execution migration adapters."""

from __future__ import annotations

from meridian.adapters.execution import RemoteExecutorConnection
from meridian.core.execution import CommandSpec, PutBytesSpec, PutTextSpec, RemoteCommandResult, RemoteTarget


class FakeExecutor:
    def __init__(self) -> None:
        self.target = RemoteTarget(host="198.51.100.30", user="admin", port=2222, transport="fake")
        self.calls: list[object] = []
        self.closed = False

    def run(self, spec: CommandSpec) -> RemoteCommandResult:
        self.calls.append(spec)
        return RemoteCommandResult(returncode=0, stdout="ok", operation_name=spec.operation_name)

    def put_bytes(self, spec: PutBytesSpec) -> RemoteCommandResult:
        self.calls.append(spec)
        return RemoteCommandResult(returncode=0, operation_name=spec.operation_name)

    def put_text(self, spec: PutTextSpec) -> RemoteCommandResult:
        self.calls.append(spec)
        return RemoteCommandResult(returncode=0, operation_name=spec.operation_name)

    def get_text(self, remote_path: str, *, timeout: int = 30, sudo: bool | None = None) -> RemoteCommandResult:
        self.calls.append((remote_path, timeout, sudo))
        return RemoteCommandResult(returncode=0, stdout="content")

    def close(self) -> None:
        self.closed = True


def test_remote_executor_connection_adapts_run_surface() -> None:
    executor = FakeExecutor()
    conn = RemoteExecutorConnection(executor)

    result = conn.run(
        "echo ok",
        timeout=5,
        sudo=True,
        cwd="/opt/meridian",
        env={"A": "B"},
        retries=2,
        retry_delay=0.1,
        ok_codes=[0, 2],
        sensitive=True,
        input="stdin",
        operation_name="probe",
    )

    spec = executor.calls[0]
    assert isinstance(spec, CommandSpec)
    assert result.stdout == "ok"
    assert conn.ip == "198.51.100.30"
    assert conn.user == "admin"
    assert conn.port == 2222
    assert spec.ok_codes == (0, 2)
    assert spec.stdin == "stdin"


def test_remote_executor_connection_preserves_file_helpers() -> None:
    executor = FakeExecutor()
    conn = RemoteExecutorConnection(executor)

    conn.put_bytes("/etc/meridian/a", b"x", mode="600", sensitive=True)
    conn.put_text("/etc/meridian/b", "hello", create_parent=True, timeout=10)
    content = conn.get_text("/etc/meridian/b")
    data = conn.get_bytes("/etc/meridian/b")
    conn.close()

    assert isinstance(executor.calls[0], PutBytesSpec)
    assert isinstance(executor.calls[1], PutTextSpec)
    assert content.stdout == "content"
    assert data == b"content"
    assert executor.closed is True
