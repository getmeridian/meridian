"""Localhost-only FastAPI app for executable Meridian Studio."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from meridian.adapters.deploy_process import run_deploy_process
from meridian.adapters.server_engine import default_server_connection_factory
from meridian.cluster import ClusterConfig
from meridian.config import SERVER_PROFILES_FILE
from meridian.core.validation import validation_errors_hint
from meridian.engine.operations import OperationManager, OperationRunner
from meridian.engine.routes import router
from meridian.engine.servers import (
    KeyProvider,
    ServerConnectionFactory,
    ServerProfileStoreLike,
    ensure_meridian_keypair,
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

    # Store all injectable services on app.state for route handlers
    app.state.csrf_token = token
    app.state.assets_dir = str(assets_dir) if assets_dir else ""
    app.state.operation_manager = operation_manager or OperationManager()
    app.state.cluster_loader = cluster_loader
    app.state.server_connection_factory = server_connection_factory
    app.state.server_key_provider = server_key_provider
    app.state.deploy_runner = deploy_runner

    default_store_factory = server_store_factory
    app.state.server_store_factory = (
        default_store_factory if default_store_factory is not None else lambda: ServerProfileStore(SERVER_PROFILES_FILE)
    )

    app.add_middleware(
        LocalEngineSecurity,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
        csrf_token=token,
    )

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

    app.include_router(router)

    if assets_dir is not None:
        app.mount("/", StaticFiles(directory=assets_dir, html=True), name="studio")
    else:
        app.add_api_route("/", _missing_assets_page, methods=["GET"], response_class=HTMLResponse)
        app.add_api_route("/studio/", _missing_assets_page, methods=["GET"], response_class=HTMLResponse)

    return app


def _allowed_hosts(host: str, port: int) -> set[str]:
    hosts = {host, "127.0.0.1", "localhost"}
    return hosts | {f"{candidate}:{port}" for candidate in hosts}


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
