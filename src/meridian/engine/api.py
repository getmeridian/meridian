"""Localhost-only FastAPI app for executable Meridian Studio."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from meridian.adapters.server_engine import default_server_connection_factory
from meridian.cluster import ClusterConfig
from meridian.config import SERVER_PROFILES_FILE, SERVERS_FILE
from meridian.core.deploy import DeployRequest
from meridian.core.schema import schema_catalog
from meridian.core.servers import (
    ServerBootstrapKeyRequest,
    ServerConnectionDraft,
    ServerKeyPolicy,
    ServerValidateRequest,
)
from meridian.core.services import collect_workflow, workflow_catalog
from meridian.core.validation import validation_errors_hint
from meridian.engine.deploy import dry_run_deploy_request
from meridian.engine.errors import EngineError
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

    server_ref: str
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
) -> FastAPI:
    """Create the localhost Engine app without starting a network listener."""
    token = csrf_token or secrets.token_urlsafe(32)
    allowed_hosts = _allowed_hosts(host, port)
    allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.csrf_token = token
    app.state.assets_dir = str(assets_dir) if assets_dir else ""
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
            "assets": {"available": assets_dir is not None, "path": str(assets_dir or "")},
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


def _dump_profile(profile: Any) -> dict[str, Any]:
    return profile.model_dump(mode="json", exclude={"key_path"})


def _dump_server_result(result: Any) -> dict[str, Any]:
    payload = result.model_dump(mode="json")
    payload["server"].pop("key_path", None)
    return payload


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
