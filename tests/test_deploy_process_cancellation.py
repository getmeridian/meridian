"""The Engine deploy process adapter cancels through a marker file, never a signal."""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from meridian.adapters.deploy_process import run_deploy_process
from meridian.core.cancellation import OperationCancelledError
from meridian.core.deploy import DeployRequest
from meridian.core.errors import EngineError


class _Handle:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.callbacks: list[Callable[[], None]] = []

    def add_event(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def on_cancel(self, callback: Callable[[], None]) -> None:
        self.callbacks.append(callback)

    def request_cancel(self) -> None:
        for callback in self.callbacks:
            callback()


class _FakeChild:
    """Stands in for `meridian deploy`, polling its --cancel-file between steps."""

    def __init__(self, argv: list[str], handle: _Handle, *, exit_code: int | None = None) -> None:
        self.cancel_file = Path(argv[argv.index("--cancel-file") + 1])
        self.handle = handle
        self.forced_exit = exit_code
        self.signalled = False
        self.returncode = 0
        self.stdout = io.StringIO()
        self.stderr = self._stderr()

    def _stderr(self) -> Iterator[str]:
        yield json.dumps(_event("provision.step.completed", "packages")) + "\n"
        self.handle.request_cancel()  # the Engine cancel arrives while a step is running
        if self.forced_exit is not None:
            self.returncode = self.forced_exit
            return
        if self.cancel_file.exists():
            self.returncode = 5
            self.stdout = io.StringIO(json.dumps(_cancelled_envelope()))
            yield json.dumps(_event("command.cancelled", "Apply stopped after 1 of 9 resource actions.")) + "\n"

    def wait(self) -> int:
        return self.returncode

    def send_signal(self, _signal: int) -> None:  # pragma: no cover - must never be called
        self.signalled = True

    terminate = kill = send_signal


def _event(event_type: str, message: str) -> dict[str, Any]:
    return {
        "schema": "meridian.event/v1",
        "operation_id": "child",
        "seq": 1,
        "time": "2026-07-14T15:00:00Z",
        "level": "info",
        "type": event_type,
        "phase": "deploy",
        "message": message,
        "data": {},
    }


def _cancelled_envelope() -> dict[str, Any]:
    return {
        "schema": "meridian.output/v1",
        "command": "deploy",
        "status": "cancelled",
        "exit_code": 5,
        "summary": {"text": "Apply stopped after 1 of 9 resource actions.", "changed": True, "counts": {}},
        "data": {
            "phase": "apply",
            "message": "Apply stopped after 1 of 9 resource actions.",
            "resume_hint": "Run the same deploy again; finished steps are observed and skipped.",
            "remote_changed": True,
        },
        "warnings": [],
        "errors": [],
    }


def _patch_child(monkeypatch: pytest.MonkeyPatch, handle: _Handle, **kwargs: Any) -> list[_FakeChild]:
    children: list[_FakeChild] = []

    def popen(argv: list[str], **_popen_kwargs: Any) -> _FakeChild:
        children.append(_FakeChild(argv, handle, **kwargs))
        return children[-1]

    monkeypatch.setattr("meridian.adapters.deploy_process.subprocess.Popen", popen)
    return children


def test_cancel_request_reaches_the_child_as_a_marker_file(monkeypatch: pytest.MonkeyPatch) -> None:
    handle = _Handle()
    children = _patch_child(monkeypatch, handle)

    with pytest.raises(OperationCancelledError) as exc_info:
        run_deploy_process(DeployRequest(ip="198.51.100.10", yes=True), handle)

    assert exc_info.value.cancellation.phase == "apply"
    assert exc_info.value.cancellation.remote_changed is True
    assert [event["type"] for event in handle.events] == ["provision.step.completed", "command.cancelled"]
    assert children[0].signalled is False
    # The private marker directory is gone; a late cancel is a harmless no-op.
    assert not children[0].cancel_file.parent.exists()
    handle.request_cancel()


def test_interrupt_without_an_envelope_is_not_reported_as_a_clean_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    handle = _Handle()
    _patch_child(monkeypatch, handle, exit_code=130)

    with pytest.raises(EngineError, match="interrupted before it reported a result"):
        run_deploy_process(DeployRequest(ip="198.51.100.10", yes=True), handle)
