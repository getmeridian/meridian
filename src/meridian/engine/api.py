"""Localhost-only FastAPI app for executable Meridian Studio."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn, Self

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from meridian.adapters.deploy_process import run_deploy_process
from meridian.adapters.server_engine import default_server_connection_factory
from meridian.cluster import ClusterConfig
from meridian.config import SERVER_PROFILES_FILE, SERVERS_FILE
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
from meridian.core.validation import validation_errors_hint
from meridian.engine.deploy import dry_run_deploy_request, resolve_deploy_target
from meridian.engine.errors import EngineError
from meridian.engine.operations import ActiveDeployOperationError, OperationManager, OperationRunner
from meridian.engine.servers import (
    KeyProvider,
    ServerConnectionFactory,
    ServerProfileStoreLike,
    bootstrap_server_key,
    ensure_meridian_keypair,
    save_server_profile,
    validate_server_connection,
)
from meridian.servers import ServerProfileStore

SAFE_FETCH_SITES = {"same-origin", "none"}
LOCAL_ENGINE_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'none'"
)


class EngineBootstrapKeyRequest(BaseModel):
    """Engine-only key bootstrap body with a short-lived password secret."""

    model_config = ConfigDict(extra="forbid", strict=True)

    server_ref: ServerReferenceValue
    key_policy: ServerKeyPolicy = "generate_meridian"
    public_key: str = ""
    disable_password_auth: bool = False
    password: str = Field(default="", max_length=4096)

    @model_validator(mode="after")
    def require_public_key_when_reusing_key(self) -> Self:
        if self.key_policy == "use_existing" and not self.public_key.strip():
            raise ValueError("Paste a public key when reusing an existing SSH key.")
        return self

    def contract(self) -> ServerBootstrapKeyRequest:
        return ServerBootstrapKeyRequest(
            server_ref=self.server_ref,
            key_policy=self.key_policy,
            public_key=self.public_key,
            disable_password_auth=self.disable_password_auth,
        )


class LocalEngineSecurity:
    """Reject browser requests that do not look same-origin to this Engine."""

    def __init__(
        self,
        app: Callable[[dict[str, Any], Callable[..., Any], Callable[..., Any]], Any],
        *,
        allowed_hosts: set[str],
        allowed_origins: set[str],
        csrf_token: str,
    ) -> None:
        self.app = app
        self.allowed_hosts = allowed_hosts
        self.allowed_origins = allowed_origins
        self.csrf_token = csrf_token

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.decode("latin1").lower(): value.decode("latin1") for key, value in scope["headers"]}
        method = str(scope["method"]).upper()
        host = headers.get("host", "")
        origin = headers.get("origin", "")
        sec_fetch_site = headers.get("sec-fetch-site", "")

        if host not in self.allowed_hosts:
            await self._reject(scope, receive, send, 403, "Host is not allowed.")
            return
        if origin and origin not in self.allowed_origins:
            await self._reject(scope, receive, send, 403, "Origin is not allowed.")
            return
        if sec_fetch_site and sec_fetch_site not in SAFE_FETCH_SITES:
            await self._reject(scope, receive, send, 403, "Cross-site browser requests are not allowed.")
            return
        if method in {"POST", "PUT", "PATCH", "DELETE"} and headers.get("x-meridian-csrf") != self.csrf_token:
            await self._reject(scope, receive, send, 403, "Missing or invalid CSRF token.")
            return

        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(
        scope: dict[str, Any],
        receive: Callable[..., Any],
        send: Callable[..., Any],
        status_code: int,
        detail: str,
    ) -> None:
        response = JSONResponse({"detail": detail}, status_code=status_code)
        await response(scope, receive, send)


def create_engine_app(
    *,
    assets_dir: Path | None,
    host: str = "127.0.0.1",
    port: int = 0,
    csrf_token: str | None = None,
    cluster_loader: Callable[[], ClusterConfig] = ClusterConfig.load,
    server_store_factory: Callable[[], ServerProfileStoreLike] | None = None,
    server_connection_factory: ServerConnectionFactory = default_server_connection_factory,
    server_key_provider: KeyProvider = ensure_meridian_keypair,
    operation_manager: OperationManager | None = None,
    deploy_runner: OperationRunner = run_deploy_process,
) -> FastAPI:
    """Create the localhost Engine app without starting a network listener."""
    token = csrf_token or secrets.token_urlsafe(32)
    allowed_hosts = _allowed_hosts(host, port)
    allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.csrf_token = token
    app.state.assets_dir = str(assets_dir) if assets_dir else ""
    app.state.operation_manager = operation_manager or OperationManager()
    app.add_middleware(
        LocalEngineSecurity,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
        csrf_token=token,
    )

    def server_store() -> ServerProfileStoreLike:
        if server_store_factory is not None:
            return server_store_factory()
        return ServerProfileStore(SERVER_PROFILES_FILE, legacy_path=SERVERS_FILE)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "message": "Invalid request body",
                    "hint": validation_errors_hint(exc.errors()),
                    "category": "user",
                }
            },
        )

    @app.middleware("http")
    async def no_store_api_responses(request: Request, call_next: Callable[[Request], Any]) -> Any:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = LOCAL_ENGINE_CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {
            "schema": "meridian.engine-health/v1",
            "status": "ok",
            "csrf_token": token,
            "assets": {"available": assets_dir is not None},
        }

    @app.get("/api/v1/schemas")
    def schemas() -> dict[str, Any]:
        return {"schema": "meridian.schema-catalog/v1", "schemas": schema_catalog(include_schemas=True)}

    @app.get("/api/v1/workflows")
    def workflows() -> dict[str, Any]:
        catalog = workflow_catalog()
        return {
            "schema": "meridian.workflow-catalog/v1",
            "workflows": [item.model_dump(mode="json") for item in catalog],
            "plans": {item.id: collect_workflow(item.id).model_dump(mode="json") for item in catalog},
        }

    @app.get("/api/v1/servers")
    def servers() -> dict[str, Any]:
        profiles = server_store().list()
        return {
            "schema": "meridian.server-profiles/v1",
            "servers": [_dump_profile(profile) for profile in profiles],
        }

    @app.post("/api/v1/servers")
    def add_server(draft: ServerConnectionDraft, x_meridian_csrf: str = Header(default="")) -> dict[str, Any]:
        _require_csrf(x_meridian_csrf, token)
        profile = save_server_profile(draft, server_store())
        return {"schema": "meridian.server-profile/v1", "server": _dump_profile(profile)}

    @app.post("/api/v1/servers/validate")
    def validate_server(request: ServerValidateRequest, x_meridian_csrf: str = Header(default="")) -> dict[str, Any]:
        _require_csrf(x_meridian_csrf, token)
        try:
            result = validate_server_connection(
                request,
                server_store(),
                connection_factory=server_connection_factory,
            )
        except EngineError as exc:
            _raise_engine_error(exc)
        return {"schema": "meridian.server-validate/v1", "result": _dump_server_result(result)}

    @app.post("/api/v1/servers/bootstrap-key")
    def bootstrap_key(
        request: EngineBootstrapKeyRequest,
        x_meridian_csrf: str = Header(default=""),
    ) -> dict[str, Any]:
        _require_csrf(x_meridian_csrf, token)
        try:
            result = bootstrap_server_key(
                request.contract(),
                server_store(),
                connection_factory=server_connection_factory,
                key_provider=server_key_provider,
                password=request.password,
            )
        except EngineError as exc:
            _raise_engine_error(exc)
        payload = result.model_dump(mode="json")
        payload["server"].pop("key_path", None)
        return {"schema": "meridian.server-bootstrap-key/v1", "result": payload}

    @app.post("/api/v1/deploy/dry-run")
    def deploy_dry_run(request: DeployRequest, x_meridian_csrf: str = Header(default="")) -> dict[str, Any]:
        _require_csrf(x_meridian_csrf, token)
        try:
            plan = dry_run_deploy_request(request, cluster=cluster_loader(), registry=server_store())
        except EngineError as exc:
            _raise_engine_error(exc)
        return {"schema": "meridian.deploy-dry-run/v1", "plan": plan.model_dump(mode="json")}

    @app.post("/api/v1/deploy/start")
    def deploy_start(request: DeployRequest, x_meridian_csrf: str = Header(default="")) -> Any:
        _require_csrf(x_meridian_csrf, token)
        if not request.yes:
            _raise_engine_error(
                EngineError(
                    "Deploy needs confirmation.",
                    hint="Review the plan in Studio, then press Start deploy.",
                    category="user",
                )
            )
        manager: OperationManager = app.state.operation_manager
        try:
            target = resolve_deploy_target(request, server_store())
        except EngineError as exc:
            _raise_engine_error(exc)
        request_target = f"direct:{target.server_ip}:{target.ssh_user}:{target.ssh_port}"
        active = manager.active_deploy_for_target(request_target)
        if active is not None:
            return _active_operation_response(active)
        try:
            operation = manager.start_deploy(request, deploy_runner, request_target=request_target)
        except ActiveDeployOperationError as exc:
            return _active_operation_response(exc.operation)
        except EngineError as exc:
            _raise_engine_error(exc)
        return {"schema": "meridian.operation-start/v1", "operation": operation.snapshot()}

    @app.get("/api/v1/operations")
    def operations() -> dict[str, Any]:
        manager: OperationManager = app.state.operation_manager
        return {
            "schema": "meridian.operations/v1",
            "operations": [operation.snapshot() for operation in manager.list()],
        }

    @app.get("/api/v1/operations/{operation_id}")
    def operation_status(operation_id: str) -> dict[str, Any]:
        operation = _get_operation(app.state.operation_manager, operation_id)
        return {"schema": "meridian.operation/v1", "operation": operation.snapshot()}

    @app.get("/api/v1/operations/{operation_id}/events")
    def operation_events(operation_id: str, after_seq: int = 0) -> dict[str, Any]:
        operation = _get_operation(app.state.operation_manager, operation_id)
        snapshot = operation.snapshot()
        return {
            "schema": "meridian.operation-events/v1",
            "operation_id": operation.id,
            "after_seq": after_seq,
            "latest_seq": snapshot["last_seq"],
            "events": operation.event_payloads(after_seq=max(after_seq, 0)),
        }

    @app.get("/api/v1/operations/{operation_id}/result")
    def operation_result(operation_id: str) -> dict[str, Any]:
        operation = _get_operation(app.state.operation_manager, operation_id)
        return {
            "schema": "meridian.operation-result/v1",
            "operation": operation.snapshot(),
            "result": operation.result,
            "error": operation.error,
        }

    @app.get("/api/v1/operations/{operation_id}/diagnostics")
    def operation_diagnostics(operation_id: str) -> dict[str, Any]:
        operation = _get_operation(app.state.operation_manager, operation_id)
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

    @app.post("/api/v1/operations/{operation_id}/cancel")
    def operation_cancel(operation_id: str, x_meridian_csrf: str = Header(default="")) -> dict[str, Any]:
        _require_csrf(x_meridian_csrf, token)
        state = app.state.operation_manager.cancel(operation_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Operation not found.")
        operation = _get_operation(app.state.operation_manager, operation_id)
        return {"schema": "meridian.operation-cancel/v1", "operation": operation.snapshot()}

    if assets_dir is not None:
        app.mount("/", StaticFiles(directory=assets_dir, html=True), name="studio")
    else:
        app.add_api_route("/", _missing_assets_page, methods=["GET"], response_class=HTMLResponse)
        app.add_api_route("/studio/", _missing_assets_page, methods=["GET"], response_class=HTMLResponse)

    return app


def _allowed_hosts(host: str, port: int) -> set[str]:
    hosts = {host, "127.0.0.1", "localhost"}
    return hosts | {f"{candidate}:{port}" for candidate in hosts}


def _require_csrf(header: str, expected: str) -> None:
    if header != expected:
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
                "operation": operation.snapshot(),
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


def _missing_assets_page() -> HTMLResponse:
    return HTMLResponse(
        """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Meridian Studio</title></head>
<body>
<main>
  <h1>Meridian Studio Engine is running</h1>
  <p>Studio assets were not found. Build the website or pass --assets-dir.</p>
</main>
</body>
</html>
"""
    )


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
