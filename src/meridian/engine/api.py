"""Localhost-only FastAPI app for executable Meridian Studio."""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from meridian.adapters.deploy_process import run_deploy_process
from meridian.adapters.server_engine import default_server_connection_factory
from meridian.cluster import ClusterConfig
from meridian.config import SERVER_PROFILES_FILE
from meridian.core.validation import validation_errors_hint
from meridian.engine.assets import STUDIO_ASSET_ROOTS
from meridian.engine.operations import OperationManager, OperationRunner
from meridian.engine.routes import router
from meridian.engine.servers import (
    KeyProvider,
    ServerConnectionFactory,
    ServerProfileStoreLike,
)
from meridian.servers import ServerProfileStore
from meridian.ssh_keys import ensure_meridian_keypair

SAFE_FETCH_SITES = {"same-origin", "none"}
HEALTH_PATH = "/api/v1/health"
# Studio sends the per-launch key in its pre-existing CSRF header.
SESSION_KEY_HEADER = "x-meridian-csrf"
SESSION_KEY_DETAIL = {
    "message": "This Studio tab has no valid key for the running Engine.",
    "hint": "Open the address `meridian studio` printed in Terminal; it ends with #k=.",
    "category": "user",
}
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
    """Reject requests that are not same-origin or do not carry this launch's Studio key.

    Any local user can reach 127.0.0.1, so the Host/Origin checks alone only stop
    browsers. Every API call except the health probe must also present the
    per-launch key that `meridian studio` hands the browser in the URL fragment;
    the Engine never serves it.
    """

    def __init__(
        self,
        app: Callable[[dict[str, Any], Callable[..., Any], Callable[..., Any]], Any],
        *,
        allowed_hosts: set[str],
        allowed_origins: set[str],
        session_key: str,
    ) -> None:
        self.app = app
        self.allowed_hosts = allowed_hosts
        self.allowed_origins = allowed_origins
        self.session_key = session_key

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Any], send: Callable[..., Any]) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.decode("latin1").lower(): value.decode("latin1") for key, value in scope["headers"]}
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
        path = str(scope.get("path", ""))
        if path.startswith("/api/") and path != HEALTH_PATH and not self._has_session_key(headers):
            await self._reject(scope, receive, send, 401, SESSION_KEY_DETAIL)
            return

        await self.app(scope, receive, send)

    def _has_session_key(self, headers: dict[str, str]) -> bool:
        presented = headers.get(SESSION_KEY_HEADER, "")
        return hmac.compare_digest(presented.encode("utf-8"), self.session_key.encode("utf-8"))

    @staticmethod
    async def _reject(
        scope: dict[str, Any],
        receive: Callable[..., Any],
        send: Callable[..., Any],
        status_code: int,
        detail: str | dict[str, str],
    ) -> None:
        response = JSONResponse({"detail": detail}, status_code=status_code)
        await response(scope, receive, send)


def create_engine_app(
    *,
    assets_dir: Path | None,
    host: str = "127.0.0.1",
    port: int = 0,
    session_key: str,
    cluster_loader: Callable[[], ClusterConfig] = ClusterConfig.load,
    server_store_factory: Callable[[], ServerProfileStoreLike] | None = None,
    server_connection_factory: ServerConnectionFactory = default_server_connection_factory,
    server_key_provider: KeyProvider = ensure_meridian_keypair,
    operation_manager: OperationManager | None = None,
    deploy_runner: OperationRunner = run_deploy_process,
) -> FastAPI:
    """Create the localhost Engine app without starting a network listener.

    *session_key* is the per-launch secret every API call except health must send.
    """
    if not session_key:
        raise ValueError("The Engine needs a non-empty session key.")
    allowed_hosts = _allowed_hosts(host, port)
    allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    manager = operation_manager or OperationManager()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        # Stopping Studio stops deploys at a safe point instead of orphaning them.
        await run_in_threadpool(manager.shutdown)

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    # Store all injectable services on app.state for route handlers
    app.state.assets_dir = str(assets_dir) if assets_dir else ""
    app.state.operation_manager = manager
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
        session_key=session_key,
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
        # Serve Studio's directories only, even from a full website build.
        app.add_api_route("/", _studio_redirect, methods=["GET"], include_in_schema=False)
        for name in STUDIO_ASSET_ROOTS:
            if (assets_dir / name).is_dir():
                app.mount(f"/{name}", StaticFiles(directory=assets_dir / name, html=name == "studio"), name=name)
    else:
        app.add_api_route("/", _missing_assets_page, methods=["GET"], response_class=HTMLResponse)
        app.add_api_route("/studio/", _missing_assets_page, methods=["GET"], response_class=HTMLResponse)

    return app


def _allowed_hosts(host: str, port: int) -> set[str]:
    hosts = {host, "127.0.0.1", "localhost"}
    return hosts | {f"{candidate}:{port}" for candidate in hosts}


def _studio_redirect() -> RedirectResponse:
    return RedirectResponse("/studio/", status_code=307)


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
