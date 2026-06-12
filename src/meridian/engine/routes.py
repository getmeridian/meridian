"""Engine API route handlers — module-level functions on a shared APIRouter.

Extracted from create_engine_app closures to remove the god-function pattern.
Each handler receives injectable services via ``request.app.state`` attributes
set by create_engine_app in api.py.
"""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from meridian.core.deploy import DeployRequest
from meridian.core.inputs import ServerReferenceValue
from meridian.core.redaction import redact
from meridian.core.schema import schema_catalog
from meridian.core.servers import (
    ServerBootstrapKeyRequest,
    ServerConnectionDraft,
    ServerKeyPolicy,
    ServerValidateRequest,
)
from meridian.core.services import collect_workflow, workflow_catalog
from meridian.engine.deploy import dry_run_deploy_request, resolve_deploy_target
from meridian.engine.errors import EngineError
from meridian.engine.operations import ActiveDeployOperationError, OperationManager
from meridian.engine.servers import (
    bootstrap_server_key,
    save_server_profile,
    validate_server_connection,
)

router = APIRouter(prefix="/api/v1")


# ---------------------------------------------------------------------------
# Request model (engine-only; password field excluded from core contract)
# ---------------------------------------------------------------------------


class EngineBootstrapKeyRequest(BaseModel):
    """Engine-only key bootstrap body with a short-lived password secret.

    Field-level validation (key_policy vs public_key) is delegated to the core
    ServerBootstrapKeyRequest contract via .contract() so the rule lives in one
    place.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    server_ref: ServerReferenceValue
    key_policy: ServerKeyPolicy = "generate_meridian"
    public_key: str = ""
    disable_password_auth: bool = False
    password: str = Field(default="", max_length=4096)

    def contract(self) -> ServerBootstrapKeyRequest:
        return ServerBootstrapKeyRequest(
            server_ref=self.server_ref,
            key_policy=self.key_policy,
            public_key=self.public_key,
            disable_password_auth=self.disable_password_auth,
        )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _require_csrf(header: str, request: Request) -> None:
    if header != request.app.state.csrf_token:
        raise HTTPException(status_code=403, detail="Missing or invalid CSRF token.")


def _raise_engine_error(exc: EngineError) -> NoReturn:
    raise HTTPException(
        status_code=400,
        detail={"message": str(exc), "hint": exc.hint, "category": exc.category},
    )


def _active_operation_response(operation: Any) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": {
                "message": "Deploy already running for this target.",
                "hint": "Resume the active operation instead of starting another deploy.",
                "category": "user",
                "operation": operation.snapshot().model_dump(mode="json"),
            }
        },
    )


def _dump_profile(profile: Any) -> dict[str, Any]:
    return profile.model_dump(mode="json", exclude={"key_path"})


def _dump_server_result(result: Any) -> dict[str, Any]:
    payload = result.model_dump(mode="json")
    payload["server"].pop("key_path", None)
    return payload


def _get_operation(manager: OperationManager, operation_id: str) -> Any:
    operation = manager.get(operation_id)
    if operation is None:
        raise HTTPException(status_code=404, detail="Operation not found.")
    return operation


def _server_store(request: Request) -> Any:
    return request.app.state.server_store_factory()


def _manager(request: Request) -> OperationManager:
    return request.app.state.operation_manager


# ---------------------------------------------------------------------------
# Read-only routes (no CSRF needed)
# ---------------------------------------------------------------------------


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    state = request.app.state
    return {
        "schema": "meridian.engine-health/v1",
        "status": "ok",
        "csrf_token": state.csrf_token,
        "assets": {"available": bool(state.assets_dir)},
    }


@router.get("/schemas")
def schemas() -> dict[str, Any]:
    return {"schema": "meridian.schema-catalog/v1", "schemas": schema_catalog(include_schemas=True)}


@router.get("/workflows")
def workflows() -> dict[str, Any]:
    catalog = workflow_catalog()
    return {
        "schema": "meridian.workflow-catalog/v1",
        "workflows": [item.model_dump(mode="json") for item in catalog],
        "plans": {item.id: collect_workflow(item.id).model_dump(mode="json") for item in catalog},
    }


@router.get("/servers")
def servers(request: Request) -> dict[str, Any]:
    store = _server_store(request)
    return {
        "schema": "meridian.server-profiles/v1",
        "servers": [_dump_profile(profile) for profile in store.list()],
    }


@router.get("/operations")
def operations(request: Request) -> dict[str, Any]:
    manager = _manager(request)
    return {
        "schema": "meridian.operations/v1",
        "operations": [operation.snapshot() for operation in manager.list()],
    }


@router.get("/operations/{operation_id}")
def operation_status(operation_id: str, request: Request) -> dict[str, Any]:
    operation = _get_operation(_manager(request), operation_id)
    return {"schema": "meridian.operation/v1", "operation": operation.snapshot()}


@router.get("/operations/{operation_id}/events")
def operation_events(operation_id: str, request: Request, after_seq: int = 0) -> dict[str, Any]:
    operation = _get_operation(_manager(request), operation_id)
    snapshot = operation.snapshot()
    return {
        "schema": "meridian.operation-events/v1",
        "operation_id": operation.id,
        "after_seq": after_seq,
        "latest_seq": snapshot.last_seq,
        "events": operation.event_payloads(after_seq=max(after_seq, 0)),
    }


@router.get("/operations/{operation_id}/result")
def operation_result(operation_id: str, request: Request) -> dict[str, Any]:
    operation = _get_operation(_manager(request), operation_id)
    return {
        "schema": "meridian.operation-result/v1",
        "operation": operation.snapshot(),
        "result": operation.result,
        "error": operation.error,
    }


@router.get("/operations/{operation_id}/diagnostics")
def operation_diagnostics(operation_id: str, request: Request) -> dict[str, Any]:
    operation = _get_operation(_manager(request), operation_id)
    snapshot = operation.snapshot()
    events = operation.event_payloads()
    payload = redact(
        {
            "schema": "meridian.operation-diagnostics/v1",
            "operation": snapshot,
            "events": events,
            "result": operation.result,
            "error": operation.error,
            "request": operation.request.model_dump(mode="json"),
        }
    )
    return {
        **payload,
        "markdown": _diagnostics_markdown(payload),
    }


# ---------------------------------------------------------------------------
# Mutation routes (CSRF required)
# ---------------------------------------------------------------------------


@router.post("/servers")
def add_server(
    draft: ServerConnectionDraft, request: Request, x_meridian_csrf: str = Header(default="")
) -> dict[str, Any]:
    _require_csrf(x_meridian_csrf, request)
    profile = save_server_profile(draft, _server_store(request))
    return {"schema": "meridian.server-profile/v1", "server": _dump_profile(profile)}


@router.post("/servers/validate")
def validate_server(
    body: ServerValidateRequest,
    request: Request,
    x_meridian_csrf: str = Header(default=""),
) -> dict[str, Any]:
    _require_csrf(x_meridian_csrf, request)
    try:
        result = validate_server_connection(
            body,
            _server_store(request),
            connection_factory=request.app.state.server_connection_factory,
        )
    except EngineError as exc:
        _raise_engine_error(exc)
    return {"schema": "meridian.server-validate/v1", "result": _dump_server_result(result)}


@router.post("/servers/bootstrap-key")
def bootstrap_key(
    body: EngineBootstrapKeyRequest,
    request: Request,
    x_meridian_csrf: str = Header(default=""),
) -> dict[str, Any]:
    _require_csrf(x_meridian_csrf, request)
    try:
        contract = body.contract()
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    try:
        result = bootstrap_server_key(
            contract,
            _server_store(request),
            connection_factory=request.app.state.server_connection_factory,
            key_provider=request.app.state.server_key_provider,
            password=body.password,
        )
    except EngineError as exc:
        _raise_engine_error(exc)
    payload = result.model_dump(mode="json")
    payload["server"].pop("key_path", None)
    return {"schema": "meridian.server-bootstrap-key/v1", "result": payload}


@router.post("/deploy/dry-run")
def deploy_dry_run(body: DeployRequest, request: Request, x_meridian_csrf: str = Header(default="")) -> dict[str, Any]:
    _require_csrf(x_meridian_csrf, request)
    try:
        plan = dry_run_deploy_request(
            body,
            cluster=request.app.state.cluster_loader(),
            registry=_server_store(request),
        )
    except EngineError as exc:
        _raise_engine_error(exc)
    return {"schema": "meridian.deploy-dry-run/v1", "plan": plan.model_dump(mode="json")}


@router.post("/deploy/start")
def deploy_start(body: DeployRequest, request: Request, x_meridian_csrf: str = Header(default="")) -> Any:
    _require_csrf(x_meridian_csrf, request)
    if not body.yes:
        _raise_engine_error(
            EngineError(
                "Deploy needs confirmation.",
                hint="Review the plan in Studio, then press Start deploy.",
                category="user",
            )
        )
    manager = _manager(request)
    try:
        target = resolve_deploy_target(body, _server_store(request))
    except EngineError as exc:
        _raise_engine_error(exc)
    request_target = f"direct:{target.server_ip}:{target.ssh_user}:{target.ssh_port}"
    active = manager.active_deploy_for_target(request_target)
    if active is not None:
        return _active_operation_response(active)
    try:
        operation = manager.start_deploy(body, request.app.state.deploy_runner, request_target=request_target)
    except ActiveDeployOperationError as exc:
        return _active_operation_response(exc.operation)
    except EngineError as exc:
        _raise_engine_error(exc)
    return {"schema": "meridian.operation-start/v1", "operation": operation.snapshot()}


@router.post("/operations/{operation_id}/cancel")
def operation_cancel(operation_id: str, request: Request, x_meridian_csrf: str = Header(default="")) -> dict[str, Any]:
    _require_csrf(x_meridian_csrf, request)
    manager = _manager(request)
    state = manager.cancel(operation_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Operation not found.")
    operation = _get_operation(manager, operation_id)
    return {"schema": "meridian.operation-cancel/v1", "operation": operation.snapshot()}


# ---------------------------------------------------------------------------
# Diagnostics markdown renderer
# ---------------------------------------------------------------------------


def _diagnostics_markdown(payload: dict[str, Any]) -> str:
    operation = payload.get("operation", {}) if isinstance(payload.get("operation"), dict) else {}
    error = payload.get("error", {}) if isinstance(payload.get("error"), dict) else {}
    lines = [
        "# Meridian Studio Operation Diagnostics",
        "",
        f"- Operation: `{operation.get('id', '')}`",
        f"- Kind: `{operation.get('kind', '')}`",
        f"- State: `{operation.get('state', '')}`",
        f"- Target: `{operation.get('request_target', '')}`",
        f"- Events: `{operation.get('event_count', 0)}`",
        f"- Warnings: `{operation.get('warning_count', 0)}`",
        f"- Errors: `{operation.get('error_count', 0)}`",
    ]
    if error:
        lines.extend(
            [
                "",
                "## Error",
                "",
                f"- Message: {error.get('message', '')}",
                f"- Hint: {error.get('hint', '')}",
                f"- Category: `{error.get('category', '')}`",
            ]
        )
    return "\n".join(lines) + "\n"
