"""Deployer-side client of the control service's admin API (`/api` under the secret path)."""

from __future__ import annotations

from types import TracebackType
from typing import Any
from urllib.parse import quote

import httpx

from meridian.control.models import (
    ApplyFenceRecord,
    ApplyFenceState,
    ApplyFenceSwap,
    ClientEndpoint,
    EnrollmentGrant,
    FleetAuthority,
    IssuedToken,
    NodeSpec,
    NodeStatus,
    OwnerStatus,
    RevisionList,
    RevisionProposal,
    RevisionRecord,
    RevisionResult,
    RevisionSummary,
    ServiceHealth,
    ServiceSettings,
    SetupLinkGrant,
    SetupLinkRequest,
    TokenCreate,
    TokenList,
    TokenRecord,
    TokenScope,
    TrafficSummary,
    UserImportBatch,
    UserImportResult,
    UserRecord,
    UserUpdate,
)
from meridian.core.errors import MeridianError

_GATEWAY_FAILURES = frozenset({502, 503, 504})


class ControlServiceError(MeridianError):
    """The control service refused a request or could not be reached."""


class ControlServiceNotFound(ControlServiceError):
    """The requested object does not exist."""


class ControlServiceConflict(ControlServiceError):
    """The request lost a race or does not fit the object's state (HTTP 409)."""


class ControlServiceAuthError(ControlServiceError):
    """The service refused the saved admin token."""


class ControlServiceScopeError(ControlServiceAuthError):
    """The token is valid, but its scope does not cover the request."""


class ControlClient:
    """Typed calls to one control service; `url` includes the secret path."""

    def __init__(self, url: str, token: str, *, timeout: float = 20.0, verify: bool | str = True) -> None:
        self._client = httpx.Client(
            base_url=url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            verify=verify,
        )

    def __enter__(self) -> ControlClient:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def health(self) -> ServiceHealth:
        return ServiceHealth.model_validate(self._call("GET", "api/health"))

    def ping(self) -> bool:
        try:
            self.health()
        except ControlServiceError:
            return False
        return True

    def settings(self) -> ServiceSettings:
        return ServiceSettings.model_validate(self._call("GET", "api/settings"))

    def put_settings(self, settings: ServiceSettings) -> ServiceSettings:
        return ServiceSettings.model_validate(self._call("PUT", "api/settings", settings.model_dump(mode="json")))

    def node(self, node_id: str) -> NodeStatus | None:
        try:
            return NodeStatus.model_validate(self._call("GET", f"api/nodes/{_segment(node_id)}"))
        except ControlServiceNotFound:
            return None

    def put_node(self, node_id: str, spec: NodeSpec) -> NodeStatus:
        body = spec.model_dump(mode="json")
        return NodeStatus.model_validate(self._call("PUT", f"api/nodes/{_segment(node_id)}", body))

    def delete_node(self, node_id: str) -> None:
        self._call("DELETE", f"api/nodes/{_segment(node_id)}")

    def grant_enrollment(self, node_id: str) -> EnrollmentGrant:
        """Mint the node's single-use enrollment token; an earlier unspent one stops working."""
        return EnrollmentGrant.model_validate(self._call("POST", f"api/nodes/{_segment(node_id)}/enrollment", {}))

    def fleet_authority(self) -> FleetAuthority:
        return FleetAuthority.model_validate(self._call("GET", "api/ca"))

    def traffic(self) -> TrafficSummary:
        return TrafficSummary.model_validate(self._call("GET", "api/traffic"))

    def get_user(self, username: str) -> UserRecord | None:
        try:
            return UserRecord.model_validate(self._call("GET", f"api/users/{_segment(username)}"))
        except ControlServiceNotFound:
            return None

    def list_users(self) -> list[UserRecord]:
        return [UserRecord.model_validate(item) for item in self._call("GET", "api/users")["users"]]

    def put_user(self, username: str) -> UserRecord:
        return UserRecord.model_validate(self._call("PUT", f"api/users/{_segment(username)}", {}))

    def import_users(self, batch: UserImportBatch) -> UserImportResult:
        """Create users with given credentials and usage; all or nothing (`ControlServiceConflict` on a clash)."""
        return UserImportResult.model_validate(self._call("POST", "api/import/users", batch.model_dump(mode="json")))

    def update_user(self, username: str, update: UserUpdate) -> UserRecord:
        body = update.model_dump(mode="json", include=update.model_fields_set)
        return UserRecord.model_validate(self._call("PATCH", f"api/users/{_segment(username)}", body))

    def reissue_user(self, username: str) -> UserRecord:
        return UserRecord.model_validate(self._call("POST", f"api/users/{_segment(username)}/reissue", {}))

    def reset_user_traffic(self, username: str) -> UserRecord:
        return UserRecord.model_validate(self._call("POST", f"api/users/{_segment(username)}/reset-traffic", {}))

    def delete_user(self, username: str) -> None:
        self._call("DELETE", f"api/users/{_segment(username)}")

    def get_endpoint(self, endpoint_id: str) -> ClientEndpoint | None:
        try:
            return ClientEndpoint.model_validate(self._call("GET", f"api/endpoints/{_segment(endpoint_id)}"))
        except ControlServiceNotFound:
            return None

    def put_endpoint(self, endpoint_id: str, endpoint: ClientEndpoint) -> ClientEndpoint:
        body = endpoint.model_dump(mode="json")
        return ClientEndpoint.model_validate(self._call("PUT", f"api/endpoints/{_segment(endpoint_id)}", body))

    def delete_endpoint(self, endpoint_id: str) -> None:
        self._call("DELETE", f"api/endpoints/{_segment(endpoint_id)}")

    def create_token(self, name: str, scope: TokenScope) -> IssuedToken:
        """Mint a device's or agent's token; the secret is in this response only."""
        body = TokenCreate(name=name, scope=scope).model_dump(mode="json")
        return IssuedToken.model_validate(self._call("POST", "api/tokens", body))

    def list_tokens(self) -> list[TokenRecord]:
        return TokenList.model_validate(self._call("GET", "api/tokens")).tokens

    def revoke_token(self, token_id: str) -> TokenRecord:
        """Revoke a token from its next request on (revoking the calling token is allowed)."""
        return TokenRecord.model_validate(self._call("DELETE", f"api/tokens/{_segment(token_id)}"))

    def owner_status(self) -> OwnerStatus:
        """Whether the web UI has an owner; `ControlServiceNotFound` from a service older than the UI."""
        return OwnerStatus.model_validate(self._call("GET", "api/owner"))

    def issue_setup_link(self, *, reset: bool = False) -> SetupLinkGrant:
        """A one-time UI setup token (root token only); `reset` replaces an existing owner's password."""
        body = SetupLinkRequest(reset=reset).model_dump(mode="json")
        return SetupLinkGrant.model_validate(self._call("POST", "api/owner/setup-link", body))

    def revisions(self) -> list[RevisionSummary]:
        """Every recorded revision, newest first, without intent snapshots."""
        return RevisionList.model_validate(self._call("GET", "api/revisions")).revisions

    def revision(self, number: int) -> RevisionRecord | None:
        try:
            return RevisionRecord.model_validate(self._call("GET", f"api/revisions/{number}"))
        except ControlServiceNotFound:
            return None

    def propose_revision(self, proposal: RevisionProposal) -> RevisionRecord:
        return RevisionRecord.model_validate(self._call("POST", "api/revisions", proposal.model_dump(mode="json")))

    def approve_revision(self, number: int) -> RevisionRecord:
        """Approve a proposed revision; a token's own proposal needs an `admin` token."""
        return RevisionRecord.model_validate(self._call("POST", f"api/revisions/{number}/approve", {}))

    def record_revision_result(self, number: int, result: RevisionResult) -> RevisionRecord:
        body = result.model_dump(mode="json")
        return RevisionRecord.model_validate(self._call("POST", f"api/revisions/{number}/result", body))

    def fence(self) -> ApplyFenceState:
        return ApplyFenceState.model_validate(self._call("GET", "api/fence"))

    def swap_fence(self, expected_version: int, record: ApplyFenceRecord) -> ApplyFenceState:
        """Replace the apply fence; `ControlServiceConflict` when it is no longer at `expected_version`."""
        body = ApplyFenceSwap(expected_version=expected_version, record=record).model_dump(mode="json")
        return ApplyFenceState.model_validate(self._call("PUT", "api/fence", body))

    def fetch_subscription(self, sub_token: str, *, document: str = "") -> str:
        """Fetch a subscription the way a client app does (the `/sub` route, no admin token needed)."""
        path = f"sub/{_segment(sub_token)}" + (f"/{document}" if document else "")
        response = self._request("GET", path)
        return response.text

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        return self._request(method, path, body).json()

    def _request(self, method: str, path: str, body: Any = None) -> httpx.Response:
        try:
            response = self._client.request(method, path, json=body)
        except httpx.RequestError as exc:
            raise ControlServiceError(
                f"Could not reach the control service: {type(exc).__name__}",
                hint="Check that the control server answers on 443, then retry.",
                category="system",
                retryable=True,
            ) from exc
        if response.status_code == 404:
            raise ControlServiceNotFound(_message(response), category="user")
        if response.status_code == 409:
            raise ControlServiceConflict(_message(response), category="user")
        if response.status_code == 403:
            raise ControlServiceScopeError(
                f"The control service refused the request: {_message(response)}",
                hint="Use a token whose scope covers this request.",
                category="user",
            )
        if response.status_code == 401:
            raise ControlServiceAuthError(
                "The control service refused the saved admin token.",
                hint="Restore the fleet's saved credentials (cluster.yml) or redeploy the control server.",
                category="user",
            )
        if response.status_code in _GATEWAY_FAILURES:
            # nginx answers for a stopped or hung service: unreachable, not a refusal.
            raise ControlServiceError(
                f"The control service did not answer (HTTP {response.status_code} from the control server's proxy).",
                hint="Check the service: meridian check; on the server, systemctl status ctld.",
                category="system",
                retryable=True,
            )
        if response.status_code >= 400:
            raise ControlServiceError(
                f"Control service error: {_message(response)}",
                category="system" if response.status_code >= 500 else "user",
            )
        return response


def _segment(value: str) -> str:
    return quote(value, safe="")


def _message(response: httpx.Response) -> str:
    try:
        error = response.json().get("error", {})
    except ValueError:
        return f"HTTP {response.status_code}"
    return str(error.get("message") or f"HTTP {response.status_code}")
