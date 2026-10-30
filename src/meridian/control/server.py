"""HTTP front of the control service, on loopback behind nginx's secret path.

nginx strips the secret path, so this server sees `/api/...` (a bearer token
whose scope covers the route's, `tokens.py`),
`/agent/v1/...` (the auth `AGENT_HTTP_SURFACE` declares per endpoint), and
`/sub/...` (the connection-page route), and the web UI (`ui_routes.py`: the page,
its files, the owner's sign-in, whose session maps to the admin scope). The
standard library's threading server is enough here: a thread per waiting
long-poll costs little at fleet sizes Meridian targets.
"""

from __future__ import annotations

import hmac
import json
import re
import shutil
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import unquote

from pydantic import BaseModel, ValidationError

from meridian import __version__
from meridian.control import ui_routes
from meridian.control.api_models import DeletedResource, DeployerStateReceipt, UserList
from meridian.control.blobs import BlobStore, fleet_artifacts, served_hashes
from meridian.control.command_models import NodeCommandRequest, UserCutOffRequest
from meridian.control.enrollment import Session, token_sha256
from meridian.control.errors import ServiceError, agent_error
from meridian.control.http_errors import api_error_response, error_page, validation_message
from meridian.control.login_limits import LockedOut
from meridian.control.models import (
    ApplyFenceSwap,
    ClientEndpoint,
    DeployerStateDeposit,
    DeviceKeyCreate,
    NodeSpec,
    PrivacySettings,
    RecoveryKeyUpdate,
    RevisionProposal,
    RevisionResult,
    ScopeRequirement,
    ServiceConfig,
    ServiceSettings,
    TokenCreate,
    UserCreate,
    UserImportBatch,
    UserPatch,
    UserRecord,
    UserUpdate,
    scope_covers,
)
from meridian.control.openapi import openapi_document
from meridian.control.operations_api import read_attention, read_events, read_history_days
from meridian.control.owner import OwnerSession
from meridian.control.route_catalog import api_route_for
from meridian.control.route_table import Route, control_routes
from meridian.control.route_types import ApiRoute
from meridian.control.service import ControlService
from meridian.control.subscriptions import unknown_subscription
from meridian.control.times import utc_now
from meridian.control.tokens import ROOT, WEB_OWNER, Caller, unauthenticated
from meridian.control.ui_routes import UI_ROUTES, UiContext, owner_session
from meridian.control.user_access import refuse_declared
from meridian.core.agent_http import AGENT_HTTP_SURFACE
from meridian.core.agent_protocol import (
    CommandResult,
    EnrollRequest,
    ObservedStateReport,
    PollRequest,
    RenewRequest,
    StatsReport,
)
from meridian.core.client_limits import ClientExtendRequest

ModelT = TypeVar("ModelT", bound=BaseModel)
MAX_BODY_BYTES = AGENT_HTTP_SURFACE.max_request_bytes
# Routes read before anyone is authenticated: an enrollment, a hello, a sign-in, or a setup step is a few KiB.
UNAUTHENTICATED_BODY_BYTES = 64 * 1024
_UNAUTHENTICATED_BODIES = re.compile(r"/auth/.*|/agent/v1/(?:enroll|hello|renew|poll)")
ENFORCE_INTERVAL_SECONDS = 30


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        service: ControlService,
        admin_token_sha256: str,
        blobs: BlobStore | None = None,
        *,
        public_path: str = "",
    ) -> None:
        super().__init__(address, _Handler)
        self.service = service
        self.admin_token_sha256 = admin_token_sha256
        # Artifacts agents fetch with source `service`; without a store every blob is unknown.
        self.blobs = blobs
        if blobs is not None:
            service.background.blob_mirror = blobs.health
        self.ui = UiContext(service, public_path)


class _Handler(BaseHTTPRequestHandler):
    server: ControlServer
    server_version = "nginx"
    sys_version = ""
    # A socket read deadline: a client that stops sending mid-request frees its thread (one request per connection).
    timeout = 60
    caller: Caller | None = None
    owner_session: OwnerSession | None = None
    api_route: ApiRoute | None = None

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

    def do_HEAD(self) -> None:
        # Subscription apps probe with HEAD; `_send` drops the body.
        self._dispatch("GET")

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """Answer malformed requests and unknown methods as nginx would, never with http.server's page."""
        self.close_connection = True
        self._send(code, "text/html", error_page(code), {"Connection": "close"})

    def log_message(self, format: str, *args: Any) -> None:
        """Keep no request log: paths carry subscription tokens and the secret path."""

    # -- dispatch --------------------------------------------------------------------

    def _dispatch(self, method: str) -> None:
        # Clients percent-encode IDs (`main%3Amain-reality`); names never contain `/`, so decoding first is safe.
        path = unquote(self.path.split("?", 1)[0])
        agent = path.startswith("/agent/")
        self.caller = None
        self.owner_session = None
        self.api_route = None
        try:
            for route_method, pattern, scope, handler in ALL_ROUTES:
                match = pattern.fullmatch(path)
                if match and route_method == method:
                    self.api_route = api_route_for(method, handler.__name__)
                    if scope is not None:
                        self.caller = self._authorize(scope)
                        if self.owner_session is not None:
                            _owner_may(handler)
                            if handler in OWNER_STEP_UP:
                                self._service.owner.require_recent(self.owner_session)
                    session = self.owner_session
                    actor = self._service.tokens.actor(self.caller).name if self.caller is not None else "system"
                    with self._service.events.as_actor(actor):
                        with self._service.owner.while_live(session) if session is not None else nullcontext():
                            result = handler(self, match)
                    if result is not None:
                        self._json(200, result)
                    return
            if path.startswith("/sub/"):
                # What a mistyped link gets must not differ from an unknown token's answer.
                unknown = unknown_subscription()
                self._send(unknown.status, unknown.content_type, unknown.body.encode("utf-8"), unknown.headers)
                return
            raise ServiceError("not_found", "Not found.", 404)
        except ServiceError as exc:
            self._error(exc, agent=agent)
        except ValidationError as exc:
            self._error(ServiceError("invalid_request", validation_message(exc), 400), agent=agent)
        except Exception:  # noqa: BLE001 - one request must never stop the service
            self._error(ServiceError("internal", "Internal error.", 500), agent=agent)

    # -- authentication ------------------------------------------------------------------

    def _bearer(self) -> str:
        header = self.headers.get("Authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise unauthenticated()
        return token.strip()

    def _authorize(self, needed: ScopeRequirement) -> Caller:
        """The request's caller: 401 for a missing, wrong, or revoked token, 403 when its scope falls short.

        A request without `Authorization` may instead carry the web owner's session
        cookie, which holds the admin scope (writes also need the UI's CSRF header).
        """
        if not self.headers.get("Authorization") and self.headers.get("Cookie"):
            owner_session(self)
            caller = WEB_OWNER
        else:
            token = self._bearer()
            if hmac.compare_digest(token_sha256(token), self.server.admin_token_sha256):
                caller = ROOT
            else:
                caller = self.server.service.tokens.authenticate(token)
        requirements = (needed,) if isinstance(needed, str) else needed
        if not any(scope_covers(caller.scope, scope) for scope in requirements):
            raise ServiceError(
                "insufficient_scope", f"This needs a token with the {' or '.join(requirements)} scope.", 403
            )
        return caller

    @property
    def _service(self) -> ControlService:
        return self.server.service

    # -- the web UI's view of this handler (`ui_routes.UiRequest`) -------------------------

    @property
    def ui(self) -> UiContext:
        return self.server.ui

    def read_body(self) -> bytes:
        return self._body()

    def respond(self, status: int, content_type: str, data: bytes, headers: dict[str, str] | None = None) -> None:
        self._send(status, content_type, data, headers)

    def _session(self) -> tuple[ControlService, Session]:
        service = self.server.service
        return service, service.authority.authenticate(self._bearer())

    # -- bodies --------------------------------------------------------------------------

    def _body(self) -> bytes:
        declared = (self.headers.get("Content-Length") or "0").strip()
        if not declared.isascii() or not declared.isdigit():
            # `read(-1)` would read to EOF, past the limit below.
            raise ServiceError("invalid_request", "Content-Length is not a byte count.", 400)
        length = int(declared)
        path = unquote(self.path.split("?", 1)[0])
        limit = UNAUTHENTICATED_BODY_BYTES if _UNAUTHENTICATED_BODIES.fullmatch(path) else MAX_BODY_BYTES
        if length > limit:
            raise ServiceError("payload_too_large", "Request body is too large.", 413)
        return self.rfile.read(length) if length else b""

    def _model(self, model: type[ModelT]) -> ModelT:
        if self.api_route is not None and self.api_route.request_model is not model:
            raise RuntimeError("The request model differs from the route catalog.")
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
        headers = headers or {}
        if "Cache-Control" not in headers:
            self.send_header("Cache-Control", "no-store")
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _error(self, exc: ServiceError, *, agent: bool) -> None:
        retryable = exc.status in (429, 500, 503)
        retry_after = exc.retry_after if exc.retry_after is not None else (5 if retryable else None)
        if agent:
            body: dict[str, Any] = {
                "type": "error",
                "protocol": "meridian.agent/v1",
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "retryable": retryable,
                    "retry_after_seconds": retry_after,
                    "server_time": utc_now(),
                },
            }
        else:
            body = api_error_response(exc).to_data()
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        # The contract mirrors `retry_after_seconds` in the HTTP header; a locked-out sign-in says when to retry.
        if agent and retry_after is not None:
            headers = {"Retry-After": str(retry_after)}
        elif isinstance(exc, LockedOut):
            headers = {"Retry-After": str(exc.retry_after)}
        else:
            headers = None
        self._send(exc.status, "application/json", data, headers)

    # -- admin API -------------------------------------------------------------------------

    def openapi(self, _match: re.Match[str]) -> Any:
        return openapi_document(self._service.health().version).to_data()

    def health(self, _match: re.Match[str]) -> Any:
        return self._service.health()

    def get_settings(self, _match: re.Match[str]) -> Any:
        return self._service.settings()

    def put_settings(self, _match: re.Match[str]) -> Any:
        return self._service.put_settings(self._model(ServiceSettings))

    def get_privacy(self, _match: re.Match[str]) -> Any:
        return self._service.privacy_state()

    def put_privacy(self, _match: re.Match[str]) -> Any:
        settings = self._model(PrivacySettings)
        session = self.owner_session
        recent = (
            self._service.conditions(lambda: self._service.owner.hold_recent(session)) if session else nullcontext()
        )
        with recent:
            return self._service.set_privacy(settings, author=self._service.tokens.actor(self._caller()).name)

    def get_node(self, match: re.Match[str]) -> Any:
        return self._service.node_status(match[1])

    def put_node(self, match: re.Match[str]) -> Any:
        spec = self._model(NodeSpec)
        status = self._service.put_node_spec(match[1], spec)
        if self.server.blobs is not None:
            self.server.blobs.want(fleet_artifacts(self._service.node_specs))
        return status

    def delete_node(self, match: re.Match[str]) -> Any:
        self._service.delete_node(match[1])
        if self.server.blobs is not None:
            self.server.blobs.want(fleet_artifacts(self._service.node_specs))
        return DeletedResource(deleted=match[1])

    def grant_enrollment(self, match: re.Match[str]) -> Any:
        return self._service.grant_enrollment(match[1])

    def issue_command(self, match: re.Match[str]) -> Any:
        request = self._model(NodeCommandRequest).root
        if self.owner_session is not None and request.kind != "restart_unit":
            raise ServiceError("insufficient_scope", "The console issues only managed-unit restarts directly.", 403)
        return self._service.issue_command(match[1], request)

    def get_command(self, match: re.Match[str]) -> Any:
        return self._service.commands.get(match[1], match[2])

    def command_targets(self, match: re.Match[str]) -> Any:
        return self._service.commands.targets(match[1])

    def fleet_authority(self, _match: re.Match[str]) -> Any:
        return self._service.authority.fleet_authority()

    def traffic(self, _match: re.Match[str]) -> Any:
        return self._service.traffic.summary()

    def events(self, _match: re.Match[str]) -> Any:
        return read_events(self._service, self.path)

    def attention(self, _match: re.Match[str]) -> Any:
        return read_attention(self._service, self.path)

    def server_usage(self, _match: re.Match[str]) -> Any:
        return self._service.server_usage()

    def daily_traffic(self, _match: re.Match[str]) -> Any:
        return self._service.traffic.daily(days=self._history_days())

    def user_usage(self, match: re.Match[str]) -> Any:
        return self._service.traffic.user_usage(match[1], days=self._history_days())

    def _history_days(self) -> int:
        return read_history_days(self.path)

    def list_users(self, _match: re.Match[str]) -> Any:
        return UserList(users=[self._visible(user) for user in self._service.list_users()])

    def get_user(self, match: re.Match[str]) -> Any:
        user = self._service.get_user(match[1])
        if user is None:
            raise ServiceError("not_found", "Unknown user.", 404)
        return self._visible(user)

    def user_connections(self, match: re.Match[str]) -> Any:
        return self._service.user_connections(match[1])

    def _visible(self, user: UserRecord) -> UserRecord:
        """A user as this caller may see them: the VLESS UUID and subscription token connect as the user."""
        if self.caller is not None and scope_covers(self.caller.scope, "people"):
            return user
        return user.model_copy(update={"uuid": "", "sub_token": "", "note": "", "tag": ""})

    def put_user(self, match: re.Match[str]) -> Any:
        return self._service.put_user(match[1])

    def create_user(self, _match: re.Match[str]) -> Any:
        return self._service.create_user(self._model(UserCreate))

    def import_users(self, _match: re.Match[str]) -> Any:
        return self._service.import_users(self._model(UserImportBatch))

    def patch_user(self, _match: re.Match[str]) -> Any:
        patch = self._model(UserPatch)
        return self._service.update_user(patch.username, patch.change)

    def update_user(self, match: re.Match[str]) -> Any:
        return self._service.update_user(match[1], self._model(UserUpdate))

    def reissue_user(self, match: re.Match[str]) -> Any:
        cut_off = self._cut_off_requested()
        with self._refuse_declared(match[1], "meridian client reissue"):
            if cut_off:
                return self._service.cut_off_user(match[1], reissue=True)
            return self._service.reissue_user(match[1])

    def cut_off_scope(self, match: re.Match[str]) -> Any:
        return self._service.cutoffs.scope(match[1])

    def _cut_off_requested(self) -> bool:
        body = self._body()
        request = UserCutOffRequest.model_validate_json(body) if body else UserCutOffRequest()
        if self.api_route is None or self.api_route.request_model is not UserCutOffRequest:
            raise RuntimeError("The cut-off request has no route declaration.")
        for condition in self.api_route.body_scopes:
            if getattr(request, condition.field) == condition.equals:
                self._authorize(condition.scope)
        return request.cut_off

    def reset_user_traffic(self, match: re.Match[str]) -> Any:
        return self._service.reset_user_traffic(match[1])

    def extend_user(self, match: re.Match[str]) -> Any:
        request = self._model(ClientExtendRequest)
        return self._service.extend_user(match[1], days=request.days)

    def user_devices(self, match: re.Match[str]) -> Any:
        return self._service.user_devices(match[1])

    def delete_user_device(self, match: re.Match[str]) -> Any:
        return self._service.delete_user_devices(match[1], match[2])

    def clear_user_devices(self, match: re.Match[str]) -> Any:
        return self._service.delete_user_devices(match[1])

    def delete_user(self, match: re.Match[str]) -> Any:
        cut_off = self._cut_off_requested()
        with self._refuse_declared(match[1], "meridian client remove"):
            if cut_off:
                return self._service.cut_off_user(match[1])
            self._service.delete_user(match[1])
        return DeletedResource(deleted=match[1])

    def _refuse_declared(self, username: str, command: str) -> AbstractContextManager[None]:
        return refuse_declared(self._service, username, command, self.caller, self.owner_session)

    def get_endpoint(self, match: re.Match[str]) -> Any:
        endpoint = self._service.get_endpoint(match[1])
        if endpoint is None:
            raise ServiceError("not_found", "Unknown endpoint.", 404)
        return endpoint

    def put_endpoint(self, match: re.Match[str]) -> Any:
        return self._service.put_endpoint(match[1], self._model(ClientEndpoint))

    def delete_endpoint(self, match: re.Match[str]) -> Any:
        self._service.delete_endpoint(match[1])
        return DeletedResource(deleted=match[1])

    def create_token(self, _match: re.Match[str]) -> Any:
        if self.caller is None:
            raise unauthenticated()
        request = self._model(TokenCreate)
        session = self.owner_session
        if session is None:
            return self._service.tokens.issue(request, issuer=self.caller)
        # An admin token would reach every route `OWNER_ROUTES` keeps from the console.
        if request.scope == "admin":
            raise ServiceError(
                "insufficient_scope", "The console can create read, propose, approve, or people tokens.", 403
            )
        owner = self._service.owner
        return self._service.tokens.issue(request, issuer=self.caller, still_allowed=lambda: owner.hold_recent(session))

    def list_tokens(self, _match: re.Match[str]) -> Any:
        return self._service.tokens.list_tokens()

    def revoke_token(self, match: re.Match[str]) -> Any:
        return self._service.tokens.revoke(match[1], caller=self._caller())

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

    # -- backups (`backups.py`) --------------------------------------------------------------

    def list_device_keys(self, _match: re.Match[str]) -> Any:
        return self._service.backups.list_devices()

    def register_device_key(self, _match: re.Match[str]) -> Any:
        return self._service.backups.register_device(self._model(DeviceKeyCreate))

    def revoke_device_key(self, match: re.Match[str]) -> Any:
        return self._service.backups.revoke_device(match[1])

    def get_recovery_key(self, _match: re.Match[str]) -> Any:
        return self._service.backups.recovery_key()

    def put_recovery_key(self, _match: re.Match[str]) -> Any:
        return self._service.backups.set_recovery_key(self._model(RecoveryKeyUpdate))

    def deposit_deployer_state(self, _match: re.Match[str]) -> Any:
        state = self._service.backups.deposit(self._model(DeployerStateDeposit))
        return DeployerStateReceipt(generation=state.generation, deposited_at=state.deposited_at)

    def snapshot(self, _match: re.Match[str]) -> None:
        self._send(200, "application/octet-stream", self._service.snapshot())

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
        token = self._bearer()
        return service.poll(
            session.node_id,
            self._model(PollRequest),
            max_wait=remaining,
            recheck=lambda: service.authority.authenticate(token),
            capabilities=session.capabilities,
        )

    def agent_report(self, _match: re.Match[str]) -> Any:
        service, session = self._session()
        body, raw = self._raw()
        report = ObservedStateReport.model_validate_json(body)
        return service.report(session.node_id, report, raw)

    def agent_stats(self, _match: re.Match[str]) -> Any:
        service, session = self._session()
        return service.record_stats(session.node_id, self._model(StatsReport))

    def agent_command_result(self, _match: re.Match[str]) -> Any:
        service, session = self._session()
        result = self._model(CommandResult)
        token = self._bearer()
        with service.conditions(lambda: service.authority.authenticate(token)):
            return service.commands.accept_result(session.node_id, result)

    def agent_blob(self, match: re.Match[str]) -> Any:
        service, session = self._session()
        blobs = self.server.blobs
        # Only an artifact of the session's own node: another node's pins are none of its business.
        spec = service.node_spec(session.node_id)
        if blobs is None or spec is None or match[1] not in served_hashes(spec):
            raise ServiceError("not_found", "Unknown blob.", 404)
        path = blobs.path(match[1])
        if path is None:
            blobs.missed(match[1])
            raise ServiceError("not_found", "Unknown blob.", 404)
        # Streamed from disk: an Xray archive is tens of megabytes.
        with path.open("rb") as handle:
            size = path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            shutil.copyfileobj(handle, self.wfile, 1 << 16)
        return None


ROUTES: list[Route] = control_routes(_Handler)


# The web UI's routes (`ui_routes.py`) live in their own block; `/api` routes come first.
ALL_ROUTES: list[Route] = [*ROUTES, *UI_ROUTES]
# Sensitive owner changes need a recent password confirmation, held again in the write's transaction.
OWNER_STEP_UP: frozenset[Callable[..., Any]] = frozenset({_Handler.create_token, _Handler.put_privacy})
# The only routes an owner session reaches: what the console's screens call ("people edit, the rest
# view"). Everything else — node, endpoint, and fence writes, revisions, enrollment, user import,
# backups — is the deployer's CLI or an API token, so a borrowed browser cannot reshape the fleet or
# take credentials, and a route added later stays closed to the console until it is listed here.
OWNER_ROUTES: frozenset[Callable[..., Any]] = frozenset(
    {
        _Handler.get_settings,
        _Handler.get_privacy,
        _Handler.put_privacy,
        _Handler.user_usage,
        _Handler.daily_traffic,
        _Handler.events,
        _Handler.attention,
        _Handler.list_users,
        _Handler.get_user,
        _Handler.user_connections,
        _Handler.create_user,
        _Handler.update_user,
        _Handler.reissue_user,
        _Handler.reset_user_traffic,
        _Handler.extend_user,
        _Handler.user_devices,
        _Handler.delete_user_device,
        _Handler.clear_user_devices,
        _Handler.delete_user,
        _Handler.cut_off_scope,
        _Handler.get_command,
        _Handler.command_targets,
        _Handler.issue_command,
        _Handler.list_tokens,
        _Handler.create_token,
        _Handler.revoke_token,
        _Handler.list_revisions,
        _Handler.get_revision,
        ui_routes.fleet,
        ui_routes.share,
    }
)


def _owner_may(handler: Callable[..., Any]) -> None:
    if handler not in OWNER_ROUTES:
        raise ServiceError(
            "insufficient_scope", "Only the meridian CLI or an API token does this; the console never needs it.", 403
        )


def serve(config_path: Path) -> None:
    """Run the service until interrupted."""
    config = ServiceConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    state_dir = Path(config.state_dir)
    service = ControlService(state_dir / "control.db", fleet_id=config.fleet_id, version=__version__)
    blobs = BlobStore(state_dir / "blobs", health=service.background.blob_mirror)
    blobs.want(fleet_artifacts(service.node_specs))
    server = ControlServer(
        (config.listen_host, config.listen_port),
        service,
        config.admin_token_sha256,
        blobs,
        public_path=config.public_path,
    )
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
        service.background.enforcement.run(service.enforce)
