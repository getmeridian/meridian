"""In-memory operation runtime for localhost Studio Engine."""

from __future__ import annotations

import secrets
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import ValidationError

from meridian.core.deploy import DeployRequest
from meridian.core.models import Event, EventLevel
from meridian.core.operations import DeployOperationResult
from meridian.core.redaction import redact
from meridian.engine.errors import EngineError

OperationState = Literal[
    "queued",
    "running",
    "succeeded",
    "failed",
    "cancel_requested",
    "cancelled",
    "completed_after_cancel",
]
OperationKind = Literal["deploy"]
OperationRunner = Callable[[DeployRequest, "EngineOperation"], dict[str, Any]]


class ActiveDeployOperationError(EngineError):
    """Raised when another deploy is already active for the same target."""

    def __init__(self, operation: "EngineOperation") -> None:
        super().__init__(
            "Deploy already running for this target.",
            hint="Resume the active operation instead of starting another deploy.",
            category="user",
        )
        self.operation = operation


class EngineOperation:
    """Mutable process-local operation record."""

    def __init__(
        self,
        *,
        operation_id: str,
        kind: OperationKind,
        request: DeployRequest,
        request_target: str | None = None,
    ) -> None:
        self.id = operation_id
        self.kind = kind
        self.request = request
        self.request_target = request_target or _deploy_target_key(request)
        self.state: OperationState = "queued"
        self.created_at = _now()
        self.updated_at = self.created_at
        self.events: list[dict[str, Any]] = []
        self.result: dict[str, Any] | None = None
        self.error: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            warning_count = sum(1 for event in self.events if event.get("level") == "warning")
            error_count = sum(1 for event in self.events if event.get("level") == "error")
            latest_event = dict(self.events[-1]) if self.events else None
            return {
                "id": self.id,
                "kind": self.kind,
                "state": self.state,
                "request_target": self.request_target,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
                "event_count": len(self.events),
                "warning_count": warning_count,
                "error_count": error_count,
                "last_seq": len(self.events),
                "current_phase": str(latest_event.get("phase") or "") if latest_event else "",
                "latest_event": latest_event,
                "cancelable": self.state in {"queued", "running"},
                "has_result": self.result is not None,
                "has_error": self.error is not None,
            }

    def event_payloads(self, *, after_seq: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(event) for event in self.events if int(event.get("seq") or 0) > after_seq]

    def add_event(self, event: Event | dict[str, Any]) -> None:
        payload = event.model_dump(mode="json", by_alias=True) if isinstance(event, Event) else dict(event)
        payload["operation_id"] = self.id
        if payload.get("schema") == "meridian.event/v1":
            try:
                payload = Event.model_validate(payload).model_dump(mode="json", by_alias=True)
            except ValidationError as exc:
                payload = {
                    "schema": "meridian.event/v1",
                    "operation_id": self.id,
                    "seq": 0,
                    "time": _now(),
                    "level": "warning",
                    "type": "warning",
                    "phase": "engine",
                    "message": "Ignored an invalid child event.",
                    "data": {"validation_error": str(exc)},
                }
        payload = redact(payload)
        with self._lock:
            payload["seq"] = len(self.events) + 1
            self.events.append(payload)
            self.updated_at = _now()

    def emit_status(
        self,
        *,
        level: EventLevel = "info",
        message: str,
        phase: str = "deploy",
        event_type: str | None = None,
    ) -> None:
        status_type = event_type or (
            "error" if level == "error" else "warning" if level == "warning" else "command.started"
        )
        self.add_event(
            {
                "schema": "meridian.event/v1",
                "operation_id": self.id,
                "seq": 0,
                "time": _now(),
                "level": level,
                "type": status_type,
                "phase": phase,
                "message": message,
                "data": {"operation": self.kind},
            }
        )

    def mark_running(self) -> bool:
        with self._lock:
            if self.state == "cancelled":
                return False
            self.state = "running"
            self.updated_at = _now()
            return True

    def mark_succeeded(self, result: dict[str, Any]) -> None:
        with self._lock:
            if self.state == "cancel_requested":
                self.result = redact(result)
                self.state = "completed_after_cancel"
                self.updated_at = _now()
                return
            self.result = redact(result)
            self.state = "succeeded"
            self.updated_at = _now()

    def mark_failed(self, error: dict[str, Any]) -> None:
        with self._lock:
            self.error = redact(error)
            self.state = "failed"
            self.updated_at = _now()

    def cancel(self) -> OperationState:
        with self._lock:
            if self.state == "queued":
                self.state = "cancelled"
            elif self.state == "running":
                self.state = "cancel_requested"
            self.updated_at = _now()
            return self.state

    def cancel_requested(self) -> bool:
        with self._lock:
            return self.state == "cancel_requested"


class OperationManager:
    """Bounded in-memory operation registry for the local Engine process."""

    def __init__(self, *, max_workers: int = 2, max_operations: int = 25) -> None:
        self._operations: dict[str, EngineOperation] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="meridian-engine")
        self._lock = threading.Lock()
        self._max_operations = max_operations

    def start_deploy(
        self,
        request: DeployRequest,
        runner: OperationRunner,
        *,
        request_target: str | None = None,
    ) -> EngineOperation:
        operation = EngineOperation(
            operation_id=f"op-{secrets.token_urlsafe(10)}",
            kind="deploy",
            request=request,
            request_target=request_target,
        )
        with self._lock:
            self._prune_locked()
            active = self._active_deploy_for_target_locked(operation.request_target)
            if active is not None:
                raise ActiveDeployOperationError(active)
            if len(self._operations) >= self._max_operations:
                raise EngineError(
                    "Too many operations are already in memory.",
                    hint="Wait for a running operation to finish, then try again.",
                    category="system",
                )
            self._operations[operation.id] = operation
        self._executor.submit(self._run_deploy, operation, runner)
        return operation

    def get(self, operation_id: str) -> EngineOperation | None:
        with self._lock:
            return self._operations.get(operation_id)

    def list(self) -> list[EngineOperation]:
        with self._lock:
            return sorted(self._operations.values(), key=lambda operation: operation.created_at, reverse=True)

    def active_deploy_for_target(self, request_target: str) -> EngineOperation | None:
        with self._lock:
            return self._active_deploy_for_target_locked(request_target)

    def cancel(self, operation_id: str) -> OperationState | None:
        operation = self.get(operation_id)
        if operation is None:
            return None
        state = operation.cancel()
        operation.emit_status(
            level="warning",
            message=(
                "Operation was cancelled before it started."
                if state == "cancelled"
                else "Cancellation requested. SSH work may finish the current step first."
            ),
        )
        return state

    def _run_deploy(self, operation: EngineOperation, runner: OperationRunner) -> None:
        if not operation.mark_running():
            return
        operation.emit_status(message="Deploy operation started.")
        try:
            result = runner(operation.request, operation)
            if result.get("schema") == "meridian.deploy-operation-result/v1":
                result = DeployOperationResult.model_validate(result).model_dump(mode="json", by_alias=True)
        except EngineError as exc:
            operation.emit_status(level="error", message=str(exc))
            operation.mark_failed(
                {
                    "message": str(exc),
                    "hint": exc.hint,
                    "category": exc.category,
                    "retryable": exc.category != "bug",
                    "last_seq": operation.snapshot()["last_seq"],
                }
            )
            return
        except Exception as exc:  # pragma: no cover - defensive API boundary
            operation.emit_status(level="error", message="Deploy failed unexpectedly.")
            operation.mark_failed(
                {
                    "message": "Deploy failed unexpectedly.",
                    "hint": str(exc),
                    "category": "bug",
                    "retryable": False,
                    "last_seq": operation.snapshot()["last_seq"],
                }
            )
            return
        operation.emit_status(
            level="warning" if operation.cancel_requested() else "info",
            event_type="warning" if operation.cancel_requested() else "command.completed",
            message=(
                "Operation stopped after the cancellation request."
                if operation.cancel_requested()
                else "Deploy operation completed."
            ),
        )
        operation.mark_succeeded(result)

    def _prune_locked(self) -> None:
        if len(self._operations) < self._max_operations:
            return
        removable = sorted(
            (
                operation
                for operation in self._operations.values()
                if operation.state in {"succeeded", "failed", "cancelled", "completed_after_cancel"}
            ),
            key=lambda operation: operation.updated_at,
        )
        for operation in removable[: max(1, len(self._operations) - self._max_operations + 1)]:
            self._operations.pop(operation.id, None)

    def _active_deploy_for_target_locked(self, request_target: str) -> EngineOperation | None:
        for operation in self._operations.values():
            if (
                operation.kind == "deploy"
                and operation.state in {"queued", "running", "cancel_requested"}
                and operation.request_target == request_target
            ):
                return operation
        return None


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _deploy_target_key(request: DeployRequest) -> str:
    if request.requested_server:
        return f"ref:{request.requested_server}"
    return f"direct:{request.ip}:{request.user}:{request.ssh_port}"
