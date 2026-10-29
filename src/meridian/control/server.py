"""HTTP front of the control service, on loopback behind nginx's secret path.

nginx strips the secret path, so this server sees `/api/...` (a bearer token
whose scope covers the route's, `tokens.py`),
`/agent/v1/...` (the auth `AGENT_HTTP_SURFACE` declares per endpoint), and
`/sub/...` (the connection-page route). The
standard library's threading server is enough here: a thread per waiting
long-poll costs little at fleet sizes Meridian targets.
"""

from __future__ import annotations

import hmac
import json
import re
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import unquote

from pydantic import BaseModel, ValidationError

from meridian import __version__
from meridian.control.enrollment import Session, token_sha256
from meridian.control.errors import ServiceError, agent_error
from meridian.control.models import (
    ApplyFenceSwap,
    ClientEndpoint,
    NodeSpec,
    RevisionProposal,
    RevisionResult,
    ServiceConfig,
    ServiceSettings,
    TokenCreate,
    TokenScope,
    UserRecord,
    UserUpdate,
    scope_covers,
)
from meridian.control.service import ControlService
from meridian.control.times import utc_now
from meridian.control.tokens import ROOT, Caller, unauthenticated
from meridian.core.agent_http import AGENT_HTTP_SURFACE
from meridian.core.agent_protocol import EnrollRequest, ObservedStateReport, PollRequest, RenewRequest, StatsReport

ModelT = TypeVar("ModelT", bound=BaseModel)
MAX_BODY_BYTES = AGENT_HTTP_SURFACE.max_request_bytes
ENFORCE_INTERVAL_SECONDS = 30
_NAME = r"([a-z0-9][a-z0-9_.:-]{0,127})"
_USERNAME = r"([A-Za-z0-9_.-]{1,64})"
_TOKEN_ID = r"([0-9a-f]{12})"
_REVISION = r"([1-9][0-9]{0,15})"
# Every write that changes what the fleet runs or who may use it needs `admin`: it is
# part of applying a revision. Revisions themselves take `propose` (record one) and
# `approve` (approve someone else's; `revisions.py`), and recording a revision's result
# or moving the apply fence is applying, so `admin`. Credentials (enrollment grants,
# API tokens) always need `admin`; reads stay `read`.
WRITE: TokenScope = "admin"


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: ControlService, admin_token_sha256: str) -> None:
        super().__init__(address, _Handler)
        self.service = service
        self.admin_token_sha256 = admin_token_sha256


# (method, path, the scope an `/api` route needs or None where the handler authenticates, handler)
Route = tuple[str, re.Pattern[str], TokenScope | None, Callable[["_Handler", re.Match[str]], Any]]


class _Handler(BaseHTTPRequestHandler):
    server: ControlServer
    server_version = "nginx"
    sys_version = ""
    caller: Caller | None = None

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def log_message(self, format: str, *args: Any) -> None:
        """Keep no request log: paths carry subscription tokens and the secret path."""

    # -- dispatch --------------------------------------------------------------------

    def _dispatch(self, method: str) -> None:
        # Clients percent-encode IDs (`main%3Amain-reality`); names never contain `/`, so decoding first is safe.
        path = unquote(self.path.split("?", 1)[0])
        agent = path.startswith("/agent/")
        self.caller = None
        try:
            for route_method, pattern, scope, handler in ROUTES:
                match = pattern.fullmatch(path)
                if match and route_method == method:
                    if scope is not None:
                        self.caller = self._authorize(scope)
                    result = handler(self, match)
                    if result is not None:
                        self._json(200, result)
                    return
            raise ServiceError("not_found", "Not found.", 404)
        except ServiceError as exc:
            self._error(exc, agent=agent)
        except ValidationError as exc:
            self._error(ServiceError("invalid_request", _validation_message(exc), 400), agent=agent)
        except Exception:  # noqa: BLE001 - one request must never stop the service
            self._error(ServiceError("internal", "Internal error.", 500), agent=agent)

    # -- authentication ------------------------------------------------------------------

    def _bearer(self) -> str:
        header = self.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise unauthenticated()
        return token.strip()

    def _authorize(self, needed: TokenScope) -> Caller:
        """The request's caller: 401 for a missing, wrong, or revoked token, 403 when its scope falls short."""
        token = self._bearer()
        if hmac.compare_digest(token_sha256(token), self.server.admin_token_sha256):
            caller = ROOT
        else:
            caller = self.server.service.tokens.authenticate(token)
        if not scope_covers(caller.scope, needed):
            raise ServiceError("insufficient_scope", f"This needs a token with the {needed} scope.", 403)
        return caller

    @property
    def _service(self) -> ControlService:
        return self.server.service

    def _session(self) -> tuple[ControlService, Session]:
        service = self.server.service
        return service, service.authority.authenticate(self._bearer())

    # -- bodies --------------------------------------------------------------------------

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise ServiceError("payload_too_large", "Request body is too large.", 413)
        return self.rfile.read(length) if length else b""

    def _model(self, model: type[ModelT]) -> ModelT:
        return model.model_validate_json(self._body())

    def _raw(self) -> tuple[bytes, Any]:
        body = self._body()
        try:
            return body, json.loads(body)
        except ValueError as exc:
            raise agent_error("invalid_request", "The body is not JSON.") from exc

    def _json(self, status: int, body: Any) -> None:
        if isinstance(body, BaseModel):
            body = body.model_dump(mode="json")
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self._send(status, "application/json", data)

    def _send(self, status: int, content_type: str, data: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _error(self, exc: ServiceError, *, agent: bool) -> None:
        retryable = exc.status in (429, 500, 503)
        if agent:
            body: dict[str, Any] = {
                "type": "error",
                "protocol": "meridian.agent/v1",
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "retryable": retryable,
                    "retry_after_seconds": 5 if retryable else None,
                    "server_time": utc_now(),
                },
            }
        else:
            body = {"error": {"code": exc.code, "message": str(exc)}}
        self._json(exc.status, body)

    # -- admin API -------------------------------------------------------------------------

    def health(self, _match: re.Match[str]) -> Any:
        return self._service.health()

    def get_settings(self, _match: re.Match[str]) -> Any:
        return self._service.settings()

    def put_settings(self, _match: re.Match[str]) -> Any:
        return self._service.put_settings(self._model(ServiceSettings))

    def get_node(self, match: re.Match[str]) -> Any:
        return self._service.node_status(match[1])

    def put_node(self, match: re.Match[str]) -> Any:
        return self._service.put_node_spec(match[1], self._model(NodeSpec))

    def delete_node(self, match: re.Match[str]) -> Any:
        self._service.delete_node(match[1])
        return {"deleted": match[1]}

    def grant_enrollment(self, match: re.Match[str]) -> Any:
        return self._service.grant_enrollment(match[1])

    def fleet_authority(self, _match: re.Match[str]) -> Any:
        return self._service.authority.fleet_authority()

    def traffic(self, _match: re.Match[str]) -> Any:
        return self._service.traffic.summary()

    def list_users(self, _match: re.Match[str]) -> Any:
        return {"users": [self._visible(user).model_dump(mode="json") for user in self._service.list_users()]}

    def get_user(self, match: re.Match[str]) -> Any:
        user = self._service.get_user(match[1])
        if user is None:
            raise ServiceError("not_found", "Unknown user.", 404)
        return self._visible(user)

    def _visible(self, user: UserRecord) -> UserRecord:
        """A user as this caller may see them: the VLESS UUID and subscription token connect as the user."""
        if self.caller is not None and scope_covers(self.caller.scope, "admin"):
            return user
        return user.model_copy(update={"uuid": "", "sub_token": ""})

    def put_user(self, match: re.Match[str]) -> Any:
        return self._service.put_user(match[1])

    def update_user(self, match: re.Match[str]) -> Any:
        return self._service.update_user(match[1], self._model(UserUpdate))

    def reissue_user(self, match: re.Match[str]) -> Any:
        return self._service.reissue_user(match[1])

    def reset_user_traffic(self, match: re.Match[str]) -> Any:
        return self._service.reset_user_traffic(match[1])

    def delete_user(self, match: re.Match[str]) -> Any:
        self._service.delete_user(match[1])
        return {"deleted": match[1]}

    def get_endpoint(self, match: re.Match[str]) -> Any:
        endpoint = self._service.get_endpoint(match[1])
        if endpoint is None:
            raise ServiceError("not_found", "Unknown endpoint.", 404)
        return endpoint

    def put_endpoint(self, match: re.Match[str]) -> Any:
        return self._service.put_endpoint(match[1], self._model(ClientEndpoint))

    def delete_endpoint(self, match: re.Match[str]) -> Any:
        self._service.delete_endpoint(match[1])
        return {"deleted": match[1]}

    def create_token(self, _match: re.Match[str]) -> Any:
        if self.caller is None:
            raise unauthenticated()
        return self._service.tokens.issue(self._model(TokenCreate), issuer=self.caller)

    def list_tokens(self, _match: re.Match[str]) -> Any:
        return self._service.tokens.list_tokens()

    def revoke_token(self, match: re.Match[str]) -> Any:
        return self._service.tokens.revoke(match[1])

    def list_revisions(self, _match: re.Match[str]) -> Any:
        return self._service.revisions.list_revisions()

    def get_revision(self, match: re.Match[str]) -> Any:
        return self._service.revisions.get(int(match[1]))

    def propose_revision(self, _match: re.Match[str]) -> Any:
        return self._service.revisions.propose(self._model(RevisionProposal), caller=self._caller())

    def approve_revision(self, match: re.Match[str]) -> Any:
        return self._service.revisions.approve(int(match[1]), caller=self._caller())

    def record_revision_result(self, match: re.Match[str]) -> Any:
        return self._service.revisions.record_result(int(match[1]), self._model(RevisionResult))

    def get_fence(self, _match: re.Match[str]) -> Any:
        return self._service.revisions.fence()

    def swap_fence(self, _match: re.Match[str]) -> Any:
        return self._service.revisions.swap_fence(self._model(ApplyFenceSwap))

    def _caller(self) -> Caller:
        if self.caller is None:
            raise unauthenticated()
        return self.caller

    # -- agent API -------------------------------------------------------------------------

    def agent_enroll(self, _match: re.Match[str]) -> Any:
        return self.server.service.authority.enroll(self._model(EnrollRequest))

    def agent_hello(self, _match: re.Match[str]) -> Any:
        service = self.server.service
        _, raw = self._raw()
        if not isinstance(raw, dict):
            raise agent_error("invalid_request", "The body is not a JSON object.")
        request, welcome = service.authority.hello(raw)
        service.node_seen(request.node_id, request.agent_version)
        return welcome

    def agent_renew(self, _match: re.Match[str]) -> Any:
        service, session = self._session()
        return service.authority.renew(session, self._model(RenewRequest))

    def agent_poll(self, _match: re.Match[str]) -> Any:
        service, session = self._session()
        # Answer before the session expires, so the agent's next request is a fresh hello.
        remaining = session.expires_at - time.time() - 5
        return service.poll(session.node_id, self._model(PollRequest), max_wait=remaining)

    def agent_report(self, _match: re.Match[str]) -> Any:
        service, session = self._session()
        body, raw = self._raw()
        report = ObservedStateReport.model_validate_json(body)
        return service.report(session.node_id, report, raw)

    def agent_stats(self, _match: re.Match[str]) -> Any:
        service, session = self._session()
        return service.traffic.record(session.node_id, self._model(StatsReport))

    def agent_blob(self, _match: re.Match[str]) -> Any:
        self._session()
        raise ServiceError("not_found", "Unknown blob.", 404)

    # -- subscriptions -----------------------------------------------------------------------

    def subscription(self, match: re.Match[str]) -> None:
        response = self.server.service.subscription(
            match[1],
            match[2] or "",
            user_agent=self.headers.get("User-Agent"),
            accept=self.headers.get("Accept"),
        )
        self._send(response.status, response.content_type, response.body.encode("utf-8"), response.headers)


ROUTES: list[Route] = [
    ("GET", re.compile(r"/api/health"), "read", _Handler.health),
    ("GET", re.compile(r"/api/settings"), "read", _Handler.get_settings),
    ("PUT", re.compile(r"/api/settings"), WRITE, _Handler.put_settings),
    ("GET", re.compile(rf"/api/nodes/{_NAME}"), "read", _Handler.get_node),
    ("PUT", re.compile(rf"/api/nodes/{_NAME}"), WRITE, _Handler.put_node),
    ("DELETE", re.compile(rf"/api/nodes/{_NAME}"), WRITE, _Handler.delete_node),
    ("POST", re.compile(rf"/api/nodes/{_NAME}/enrollment"), "admin", _Handler.grant_enrollment),
    ("GET", re.compile(r"/api/ca"), "read", _Handler.fleet_authority),
    ("GET", re.compile(r"/api/traffic"), "read", _Handler.traffic),
    ("GET", re.compile(r"/api/users"), "read", _Handler.list_users),
    ("GET", re.compile(rf"/api/users/{_USERNAME}"), "read", _Handler.get_user),
    ("PUT", re.compile(rf"/api/users/{_USERNAME}"), WRITE, _Handler.put_user),
    ("PATCH", re.compile(rf"/api/users/{_USERNAME}"), WRITE, _Handler.update_user),
    ("POST", re.compile(rf"/api/users/{_USERNAME}/reissue"), WRITE, _Handler.reissue_user),
    ("POST", re.compile(rf"/api/users/{_USERNAME}/reset-traffic"), WRITE, _Handler.reset_user_traffic),
    ("DELETE", re.compile(rf"/api/users/{_USERNAME}"), WRITE, _Handler.delete_user),
    ("GET", re.compile(rf"/api/endpoints/{_NAME}"), "read", _Handler.get_endpoint),
    ("PUT", re.compile(rf"/api/endpoints/{_NAME}"), WRITE, _Handler.put_endpoint),
    ("DELETE", re.compile(rf"/api/endpoints/{_NAME}"), WRITE, _Handler.delete_endpoint),
    ("GET", re.compile(r"/api/tokens"), "admin", _Handler.list_tokens),
    ("POST", re.compile(r"/api/tokens"), "admin", _Handler.create_token),
    ("DELETE", re.compile(rf"/api/tokens/{_TOKEN_ID}"), "admin", _Handler.revoke_token),
    ("GET", re.compile(r"/api/revisions"), "read", _Handler.list_revisions),
    ("POST", re.compile(r"/api/revisions"), "propose", _Handler.propose_revision),
    ("GET", re.compile(rf"/api/revisions/{_REVISION}"), "read", _Handler.get_revision),
    ("POST", re.compile(rf"/api/revisions/{_REVISION}/approve"), "approve", _Handler.approve_revision),
    ("POST", re.compile(rf"/api/revisions/{_REVISION}/result"), WRITE, _Handler.record_revision_result),
    ("GET", re.compile(r"/api/fence"), "read", _Handler.get_fence),
    ("PUT", re.compile(r"/api/fence"), WRITE, _Handler.swap_fence),
    ("POST", re.compile(r"/agent/v1/enroll"), None, _Handler.agent_enroll),
    ("POST", re.compile(r"/agent/v1/hello"), None, _Handler.agent_hello),
    ("POST", re.compile(r"/agent/v1/renew"), None, _Handler.agent_renew),
    ("POST", re.compile(r"/agent/v1/poll"), None, _Handler.agent_poll),
    ("POST", re.compile(r"/agent/v1/report"), None, _Handler.agent_report),
    ("POST", re.compile(r"/agent/v1/stats"), None, _Handler.agent_stats),
    ("GET", re.compile(r"/agent/v1/blobs/([0-9a-f]{64})"), None, _Handler.agent_blob),
    ("GET", re.compile(r"/sub/([A-Za-z0-9_-]{16,64})(?:/(json|mihomo|singbox))?"), None, _Handler.subscription),
]


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "Invalid request."
    location = ".".join(str(part) for part in errors[0]["loc"])
    return f"Invalid request: {location} {errors[0]['msg']}".strip()[:500]


def serve(config_path: Path) -> None:
    """Run the service until interrupted."""
    config = ServiceConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    state_dir = Path(config.state_dir)
    service = ControlService(state_dir / "control.db", fleet_id=config.fleet_id, version=__version__)
    server = ControlServer((config.listen_host, config.listen_port), service, config.admin_token_sha256)
    stop = threading.Event()
    threading.Thread(target=_enforce_forever, args=(service, stop), daemon=True).start()
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()


def _enforce_forever(service: ControlService, stop: threading.Event) -> None:
    """Expire users on time even when nothing else changes."""
    while not stop.wait(ENFORCE_INTERVAL_SECONDS):
        try:
            service.enforce()
        except Exception:  # noqa: BLE001 - the next tick retries; the service must keep serving
            continue
