"""Tests for transport-neutral remote execution contracts."""

from __future__ import annotations

from meridian.adapters.ssh import SSHRemoteExecutor
from meridian.core.execution import CommandSpec, PutBytesSpec, PutTextSpec
from meridian.ssh import CommandResult


class FakeConnection:
    ip = "198.51.100.20"
    user = "admin"
    port = 2222
    local_mode = False

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.closed = False

    def run(self, *args: object, **kwargs: object) -> CommandResult:
        self.calls.append(("run", args, kwargs))
        return CommandResult(
            args=["ssh", "admin@198.51.100.20"],
            returncode=0,
            stdout="ok",
            duration_ms=12,
            attempts=2,
            redacted_command="echo ok",
            operation_name=str(kwargs.get("operation_name", "")),
        )

    def put_bytes(self, *args: object, **kwargs: object) -> CommandResult:
        self.calls.append(("put_bytes", args, kwargs))
        return CommandResult(args=["write"], returncode=0, operation_name=str(kwargs.get("operation_name", "")))

    def put_text(self, *args: object, **kwargs: object) -> CommandResult:
        self.calls.append(("put_text", args, kwargs))
        return CommandResult(args=["write"], returncode=0, operation_name=str(kwargs.get("operation_name", "")))

    def get_text(self, *args: object, **kwargs: object) -> CommandResult:
        self.calls.append(("get_text", args, kwargs))
        return CommandResult(args=["cat"], returncode=0, stdout="content")

    def close(self) -> None:
        self.closed = True


def test_ssh_remote_executor_maps_command_spec() -> None:
    conn = FakeConnection()
    executor = SSHRemoteExecutor(conn)  # type: ignore[arg-type]

    result = executor.run(
        CommandSpec(
            command="echo ok",
            timeout=5,
            sudo=True,
            cwd="/opt/meridian",
            env={"A": "B"},
            retries=2,
            retry_delay=0.1,
            ok_codes=(0, 2),
            sensitive=True,
            stdin="input",
            operation_name="probe",
        )
    )

    assert result.ok is True
    assert result.stdout == "ok"
    assert result.attempts == 2
    assert conn.calls == [
        (
            "run",
            ("echo ok",),
            {
                "timeout": 5,
                "sudo": True,
                "cwd": "/opt/meridian",
                "env": {"A": "B"},
                "retries": 2,
                "retry_delay": 0.1,
                "ok_codes": (0, 2),
                "sensitive": True,
                "input": "input",
                "operation_name": "probe",
            },
        )
    ]


def test_ssh_remote_executor_maps_file_and_target_operations() -> None:
    conn = FakeConnection()
    executor = SSHRemoteExecutor(conn)  # type: ignore[arg-type]

    target = executor.target
    assert target.host == "198.51.100.20"
    assert target.user == "admin"
    assert target.port == 2222
    assert target.transport == "ssh"

    executor.put_bytes(PutBytesSpec(remote_path="/etc/meridian/a", data=b"x", mode="600", sensitive=True))
    executor.put_text(PutTextSpec(remote_path="/etc/meridian/b", text="hello", create_parent=True))
    content = executor.get_text("/etc/meridian/b")
    executor.close()

    assert content.stdout == "content"
    assert conn.closed is True
    assert [call[0] for call in conn.calls] == ["put_bytes", "put_text", "get_text"]
