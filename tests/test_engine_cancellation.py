"""Engine cancel endpoint and cooperative cancellation in the operation runner."""

from __future__ import annotations

import time
from threading import Event
from typing import Any

from fastapi.testclient import TestClient

from meridian.core.cancellation import OperationCancellation, OperationCancelledError
from meridian.core.deploy import DeployRequest, DeployResult
from meridian.engine.api import create_engine_app
from meridian.engine.operations import EngineOperation, OperationManager

_CSRF = {"x-meridian-csrf": "test-csrf"}


def _client(**kwargs: object) -> TestClient:
    app = create_engine_app(assets_dir=None, host="127.0.0.1", port=8765, csrf_token="test-csrf", **kwargs)
    return TestClient(app, base_url="http://127.0.0.1:8765")


def _start(client: TestClient, ip: str = "198.51.100.10") -> str:
    response = client.post("/api/v1/deploy/start", json={"ip": ip, "yes": True}, headers=_CSRF)
    assert response.status_code == 200, response.text
    return response.json()["operation"]["id"]


def _cancel(client: TestClient, operation_id: str) -> dict[str, Any]:
    response = client.post(f"/api/v1/operations/{operation_id}/cancel", json={}, headers=_CSRF)
    assert response.status_code == 200, response.text
    return response.json()


def _wait_terminal(client: TestClient, operation_id: str) -> dict[str, Any]:
    for _ in range(80):
        payload = client.get(f"/api/v1/operations/{operation_id}/result").json()
        if payload["operation"]["state"] in {"succeeded", "failed", "cancelled", "completed_after_cancel"}:
            return payload
        time.sleep(0.025)
    raise AssertionError("operation did not finish")


def _event_types(client: TestClient, operation_id: str) -> list[str]:
    return [event["type"] for event in client.get(f"/api/v1/operations/{operation_id}/events").json()["events"]]


def _result(request: DeployRequest) -> dict[str, Any]:
    result = DeployResult(
        mode="first_deploy",
        server_ip=request.ip,
        ssh_user=request.user,
        ssh_port=request.ssh_port,
        domain="",
        sni="www.microsoft.com",
        client_name="default",
        harden=True,
        warp=False,
        geo_block=True,
        panel_url="https://198.51.100.10:9443",
        panel_secret_path="/secret",
        connection_page_path="/join",
        node_count=1,
        relay_count=0,
        summary="done",
    )
    return {"schema": "meridian.deploy-operation-result/v1", "result": result.model_dump(mode="json")}


def test_cancel_unknown_operation_is_404_and_requires_csrf() -> None:
    client = _client(deploy_runner=lambda _request, _operation: {})

    missing = client.post("/api/v1/operations/op-missing/cancel", json={}, headers=_CSRF)
    no_csrf = client.post("/api/v1/operations/op-missing/cancel", json={})

    assert missing.status_code == 404
    assert no_csrf.status_code == 403


def test_cancel_between_steps_stops_the_runner_with_a_typed_cancelled_state() -> None:
    step_running = Event()
    release_step = Event()
    steps_run: list[str] = []

    def runner(_request: DeployRequest, operation: EngineOperation) -> dict[str, Any]:
        requested = Event()
        operation.on_cancel(requested.set)
        for index, step in enumerate(["packages", "docker", "nginx"]):
            if requested.is_set():
                raise OperationCancelledError(
                    OperationCancellation(
                        phase="apply",
                        message=f"Apply stopped before {step}.",
                        remote_changed=index > 0,
                    )
                )
            steps_run.append(step)
            if step == "packages":
                step_running.set()
                # The in-flight step finishes even though cancel arrives meanwhile.
                assert release_step.wait(timeout=2)
        return {}

    client = _client(deploy_runner=runner)
    operation_id = _start(client)
    assert step_running.wait(timeout=2)

    first = _cancel(client, operation_id)
    second = _cancel(client, operation_id)
    release_step.set()
    payload = _wait_terminal(client, operation_id)

    assert first["schema"] == "meridian.operation-cancel/v1"
    assert first["outcome"] == "cancel_requested"
    assert first["operation"]["state"] == "cancel_requested"
    assert first["operation"]["cancelable"] is False
    assert second["outcome"] == "already_requested"
    assert steps_run == ["packages"]
    assert payload["operation"]["state"] == "cancelled"
    assert payload["operation"]["cancellation"] == {
        "phase": "apply",
        "message": "Apply stopped before docker.",
        "resume_hint": "Run the same deploy again; finished steps are observed and skipped.",
        "remote_changed": True,
    }
    assert payload["error"] is None and payload["result"] is None
    types = _event_types(client, operation_id)
    assert types.count("command.cancel_requested") == 1
    assert types[-1] == "command.cancelled"


def test_cancel_before_start_never_runs_the_deploy() -> None:
    blocker_started = Event()
    release_blocker = Event()
    calls: list[str] = []

    def runner(request: DeployRequest, _operation: EngineOperation) -> dict[str, Any]:
        calls.append(request.ip)
        if request.ip == "198.51.100.10":
            blocker_started.set()
            assert release_blocker.wait(timeout=2)
        return _result(request)

    client = _client(deploy_runner=runner, operation_manager=OperationManager(max_workers=1))
    blocker = _start(client, "198.51.100.10")
    assert blocker_started.wait(timeout=2)
    queued = _start(client, "198.51.100.20")

    response = _cancel(client, queued)
    release_blocker.set()
    _wait_terminal(client, blocker)
    payload = _wait_terminal(client, queued)

    assert response["outcome"] == "cancelled"
    assert payload["operation"]["state"] == "cancelled"
    assert payload["operation"]["cancellation"]["phase"] == "queued"
    assert payload["operation"]["cancellation"]["remote_changed"] is False
    assert calls == ["198.51.100.10"]
    assert _event_types(client, queued) == ["command.cancelled"]


def test_cancel_after_completion_is_a_no_op() -> None:
    client = _client(deploy_runner=lambda request, _operation: _result(request))
    operation_id = _start(client)
    finished = _wait_terminal(client, operation_id)
    events_before = _event_types(client, operation_id)

    response = _cancel(client, operation_id)

    assert response["outcome"] == "already_finished"
    assert response["operation"]["state"] == "succeeded"
    assert response["operation"]["updated_at"] == finished["operation"]["updated_at"]
    assert response["operation"]["cancellation"] is None
    assert _event_types(client, operation_id) == events_before


def test_on_cancel_registered_after_the_request_fires_immediately() -> None:
    operation = EngineOperation(operation_id="op-test", kind="deploy", request=DeployRequest(ip="198.51.100.10"))
    assert operation.mark_running()
    assert operation.cancel() == "cancel_requested"
    fired: list[str] = []

    operation.on_cancel(lambda: fired.append("late"))

    assert fired == ["late"]


def test_cancel_request_fires_registered_callbacks_once() -> None:
    operation = EngineOperation(operation_id="op-test", kind="deploy", request=DeployRequest(ip="198.51.100.10"))
    assert operation.mark_running()
    fired: list[str] = []
    operation.on_cancel(lambda: fired.append("early"))

    operation.cancel()
    operation.cancel()

    assert fired == ["early"]


def test_engine_shutdown_cancels_running_deploys_and_waits_for_a_safe_point() -> None:
    started = Event()
    saw_cancel = Event()

    def runner(request: DeployRequest, operation: Any) -> dict[str, Any]:
        operation.on_cancel(saw_cancel.set)
        started.set()
        assert saw_cancel.wait(5), "shutdown never requested cancellation"
        raise OperationCancelledError(
            OperationCancellation(phase="apply", message="Stopped because Studio is shutting down.")
        )

    manager = OperationManager()
    with TestClient(
        create_engine_app(
            assets_dir=None,
            host="127.0.0.1",
            port=8765,
            csrf_token="test-csrf",
            operation_manager=manager,
            deploy_runner=runner,
        ),
        base_url="http://127.0.0.1:8765",
    ) as client:
        operation_id = _start(client)
        assert started.wait(5)

    # Leaving the client runs the app's shutdown: the deploy was cancelled and finished.
    assert manager.get(operation_id).state == "cancelled"
