"""Deployer-side client of the control service's admin API (`/api` under the secret path)."""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from meridian.control.account_schedule_models import UserSchedulePreviewRequest
from meridian.control.command_models import (
    CommandRequest,
    CutOffPlan,
    NodeCommandRecord,
    NodeCommandTargets,
    UserCutOffMutation,
    UserCutOffRequest,
)
from meridian.control.credential_models import UserCredentialRotation
from meridian.control.models import (
    ApplyFenceRecord,
    ApplyFenceState,
    ApplyFenceSwap,
    ClientEndpoint,
    DeployerStateDeposit,
    DeviceKeyCreate,
    DeviceKeyList,
    DeviceKeyRecord,
    EnrollmentGrant,
    FleetAuthority,
    IssuedToken,
    NodeSpec,
    NodeStatus,
    OwnerStatus,
    PrivacySettings,
    PrivacyState,
    PrivacyUpdateResult,
    RecoveryKeyState,
    RecoveryKeyUpdate,
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
    TrafficDailySummary,
    TrafficSummary,
    UserCreate,
    UserDevices,
    UserImportBatch,
    UserImportResult,
    UserRecord,
    UserUpdate,
    UserUsage,
)
from meridian.core.account_schedule import AccountSchedulePreview
from meridian.core.attention import AttentionList, AttentionThresholds
from meridian.core.client_devices import DevicesRemoved
from meridian.core.client_limits import ClientExtendRequest
from meridian.core.client_sessions import CredentialRotationRequest, UserFlowBlocks
from meridian.core.clients import ClientConnections
from meridian.core.errors import MeridianError
from meridian.core.fleet_events import FleetEventPage, FleetEventQuery
from meridian.core.languages import LanguageSettings, LanguageState
from meridian.core.notices import NoticeState, NoticeWrite
from meridian.core.people_bulk import BulkCommitRequest, BulkPreview, BulkPreviewRequest, BulkReceipt, BulkUndoRequest
from meridian.core.people_query import PeoplePage, PeopleQuery
from meridian.core.server_allowances import ServerUsageList
from meridian.core.subscription_preview import SubscriptionPreview, SubscriptionPreviewRequest

_GATEWAY_FAILURES = frozenset({502, 503, 504})
# Transport errors raised before any request bytes left this machine.
_NEVER_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.UnsupportedProtocol)
# Told the service URL after every request that changed the service's state, or may have
# (a lost reply or a 5xx), so the CLI can pull one fresh snapshot once a command is done.
MutationListener = Callable[[str], None]
_mutation_listeners: list[MutationListener] = []


def add_mutation_listener(listener: MutationListener) -> Callable[[], None]:
    """Call `listener(url)` after each write that changed state or may have; returns the function that stops it."""
    _mutation_listeners.append(listener)

    def remove() -> None:
        if listener in _mutation_listeners:
            _mutation_listeners.remove(listener)

    return remove


class ControlServiceError(MeridianError):
    """The control service refused a request or could not be reached."""


class ControlServiceUncertain(ControlServiceError):
    """A write that may have reached the service failed without an answer: it may have taken effect."""


class ControlServiceNotFound(ControlServiceError):
    """The requested object does not exist."""


class ControlServiceConflict(ControlServiceError):
    """The request lost a race or does not fit the object's state (HTTP 409)."""


class ControlServiceCredentialsChanged(ControlServiceConflict):
    """A stale credential generation cannot rotate again after another change."""


class ControlServiceHistoryOff(ControlServiceConflict):
    """Daily usage was requested while the operator keeps history off."""


class ControlServiceAuthError(ControlServiceError):
    """The service refused the saved admin token."""


class ControlServiceScopeError(ControlServiceAuthError):
    """The token is valid, but its scope does not cover the request."""


class ControlClient:
    """Typed calls to one control service; `url` includes the secret path."""

    def __init__(
        self, url: str, token: str, *, timeout: float = 20.0, verify: bool | str = True, report_mutations: bool = True
    ) -> None:
        self.url = url.rstrip("/")
        self._report_mutations = report_mutations
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

    def issue_command(self, node_id: str, request: CommandRequest) -> NodeCommandRecord:
        return NodeCommandRecord.model_validate(
            self._call("POST", f"api/nodes/{quote(node_id, safe='')}/commands", request.model_dump(mode="json"))
        )

    def command(self, node_id: str, command_id: str) -> NodeCommandRecord:
        return NodeCommandRecord.model_validate(
            self._call("GET", f"api/nodes/{quote(node_id, safe='')}/commands/{quote(command_id, safe='')}")
        )

    def command_targets(self, node_id: str) -> NodeCommandTargets:
        return NodeCommandTargets.model_validate(
            self._call("GET", f"api/nodes/{quote(node_id, safe='')}/command-targets")
        )

    def cut_off_scope(self, username: str) -> CutOffPlan:
        return CutOffPlan.model_validate(self._call("GET", f"api/users/{quote(username, safe='')}/cut-off"))

    def user_flow_blocks(self, username: str) -> UserFlowBlocks:
        return UserFlowBlocks.model_validate(self._call("GET", f"api/users/{quote(username, safe='')}/flow-blocks"))

    def rotate_credentials(self, username: str, request: CredentialRotationRequest) -> UserCredentialRotation:
        request = CredentialRotationRequest.model_validate(request.model_dump(mode="json"))
        return UserCredentialRotation.model_validate(
            self._call("POST", f"api/users/{quote(username, safe='')}/rotate-credentials", request.to_data())
        )

    def reissue_user_with_cut_off(self, username: str) -> UserCutOffMutation:
        return UserCutOffMutation.model_validate(
            self._call(
                "POST", f"api/users/{quote(username, safe='')}/reissue", UserCutOffRequest(cut_off=True).to_data()
            )
        )

    def ping(self) -> bool:
        try:
            self.health()
        except ControlServiceError:
            return False
        return True

    def settings(self) -> ServiceSettings:
        return ServiceSettings.model_validate(self._call("GET", "api/settings"))

    def server_usage(self) -> ServerUsageList:
        return ServerUsageList.model_validate(self._call("GET", "api/servers"))

    def put_settings(self, settings: ServiceSettings) -> ServiceSettings:
        return ServiceSettings.model_validate(self._call("PUT", "api/settings", settings.model_dump(mode="json")))

    def privacy(self) -> PrivacyState:
        return PrivacyState.model_validate(self._call("GET", "api/privacy"))

    def notice(self, for_user: str | None = None) -> NoticeState:
        query = "?" + urlencode({"for": for_user}) if for_user is not None else ""
        return NoticeState.model_validate(self._call("GET", "api/notice" + query))

    def set_notice(self, request: NoticeWrite) -> NoticeState:
        method = "PUT" if request.confirm else "GET"
        return NoticeState.model_validate(
            self._request("PUT", "api/notice", request.model_dump(mode="json"), mutation_method=method).json()
        )

    def language(self) -> LanguageState:
        return LanguageState.model_validate(self._call("GET", "api/language"))

    def set_language(self, settings: LanguageSettings) -> LanguageState:
        return LanguageState.model_validate(self._call("PUT", "api/language", settings.to_data()))

    def preview_subscription(self, request: SubscriptionPreviewRequest) -> SubscriptionPreview:
        return SubscriptionPreview.model_validate(
            self._request("POST", "api/subscription/preview", request.to_data(), mutation_method="GET").json()
        )

    def preview_user_schedule(self, request: UserSchedulePreviewRequest) -> AccountSchedulePreview:
        return AccountSchedulePreview.model_validate(
            self._request(
                "POST", "api/users/preview", request.model_dump(include=request.model_fields_set), mutation_method="GET"
            ).json()
        )

    def set_privacy(self, settings: PrivacySettings) -> PrivacyUpdateResult:
        return PrivacyUpdateResult.model_validate(self._call("PUT", "api/privacy", settings.model_dump(mode="json")))

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

    def events(self, *, after: int = 0, limit: int = 100) -> FleetEventPage:
        query = FleetEventQuery(after=after, limit=limit)
        return FleetEventPage.model_validate(self._call("GET", f"api/events?after={query.after}&limit={query.limit}"))

    def attention(self, thresholds: AttentionThresholds | None = None) -> AttentionList:
        query = thresholds or AttentionThresholds()
        return AttentionList.model_validate(self._call("GET", "api/attention?" + urlencode(query.model_dump())))

    def user_usage(self, username: str, *, days: int = 30) -> UserUsage:
        return UserUsage.model_validate(self._call("GET", f"api/users/{_segment(username)}/usage?days={days}"))

    def daily_traffic(self, *, days: int = 30) -> TrafficDailySummary:
        return TrafficDailySummary.model_validate(self._call("GET", f"api/traffic/daily?days={days}"))

    def get_user(self, username: str) -> UserRecord | None:
        try:
            return UserRecord.model_validate(self._call("GET", f"api/users/{_segment(username)}"))
        except ControlServiceNotFound:
            return None

    def query_people(self, query: PeopleQuery) -> PeoplePage:
        params = {
            key: str(int(value)) if isinstance(value, bool) else value
            for key, value in query.model_dump().items()
            if value is not None
        }
        return PeoplePage.model_validate(self._call("GET", "api/people?" + urlencode(params)))

    def preview_bulk(self, request: BulkPreviewRequest) -> BulkPreview:
        return BulkPreview.model_validate(
            self._request(
                "POST", "api/people/bulk/preview", request.model_dump(mode="json"), mutation_method="GET"
            ).json()
        )

    def commit_bulk(self, request: BulkCommitRequest) -> BulkReceipt:
        return BulkReceipt.model_validate(self._call("POST", "api/people/bulk/commit", request.model_dump(mode="json")))

    def bulk_receipt(self, receipt_id: str) -> BulkReceipt:
        return BulkReceipt.model_validate(self._call("GET", "api/people/bulk/receipts/" + _segment(receipt_id)))

    def undo_bulk(self, receipt_id: str) -> BulkReceipt:
        return BulkReceipt.model_validate(
            self._call("POST", "api/people/bulk/undo", BulkUndoRequest(receipt_id=receipt_id).model_dump())
        )

    def list_users(self) -> list[UserRecord]:
        return [UserRecord.model_validate(item) for item in self._call("GET", "api/users")["users"]]

    def user_connections(self, username: str) -> ClientConnections:
        return ClientConnections.model_validate(self._call("GET", f"api/users/{_segment(username)}/connections"))

    def put_user(self, username: str) -> UserRecord:
        return UserRecord.model_validate(self._call("PUT", f"api/users/{_segment(username)}", {}))

    def create_user(self, create: UserCreate) -> UserRecord:
        """Create an account with its first settings in one transaction; an existing name is a conflict."""
        return UserRecord.model_validate(self._call("POST", "api/users", create.model_dump(mode="json")))

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

    def extend_user(self, username: str, *, days: int) -> UserRecord:
        request = ClientExtendRequest(days=days)
        return UserRecord.model_validate(
            self._call("POST", f"api/users/{_segment(username)}/extend", request.model_dump(mode="json"))
        )

    def user_devices(self, username: str) -> UserDevices:
        return UserDevices.model_validate(self._call("GET", f"api/users/{_segment(username)}/devices"))

    def delete_user_devices(self, username: str, device_key: str | None = None) -> DevicesRemoved:
        path = f"api/users/{_segment(username)}/devices"
        if device_key is not None:
            path += "/" + _segment(device_key)
        return DevicesRemoved.model_validate(self._call("DELETE", path))

    def delete_user(self, username: str) -> None:
        self._call("DELETE", f"api/users/{_segment(username)}")

    def delete_user_with_cut_off(self, username: str) -> UserCutOffMutation:
        return UserCutOffMutation.model_validate(
            self._call("DELETE", f"api/users/{_segment(username)}", UserCutOffRequest(cut_off=True).to_data())
        )

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

    def create_token(
        self, name: str, scope: TokenScope, *, expires_in_days: int | None = None, expires_at: str | None = None
    ) -> IssuedToken:
        """Mint a device's or agent's token; the secret is in this response only."""
        body = TokenCreate(name=name, scope=scope, expires_in_days=expires_in_days, expires_at=expires_at).model_dump(
            mode="json"
        )
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

    def device_keys(self) -> list[DeviceKeyRecord]:
        return DeviceKeyList.model_validate(self._call("GET", "api/backup/devices")).devices

    def register_device_key(self, name: str, public_key: str) -> DeviceKeyRecord:
        body = DeviceKeyCreate(name=name, public_key=public_key).model_dump(mode="json")
        return DeviceKeyRecord.model_validate(self._call("POST", "api/backup/devices", body))

    def revoke_device_key(self, device_id: str) -> DeviceKeyRecord:
        return DeviceKeyRecord.model_validate(self._call("DELETE", f"api/backup/devices/{_segment(device_id)}"))

    def recovery_key(self) -> RecoveryKeyState:
        return RecoveryKeyState.model_validate(self._call("GET", "api/backup/recovery-key"))

    def set_recovery_key(self, public_key: str) -> RecoveryKeyState:
        body = RecoveryKeyUpdate(public_key=public_key).model_dump(mode="json")
        return RecoveryKeyState.model_validate(self._call("PUT", "api/backup/recovery-key", body))

    def deposit_deployer_state(self, deposit: DeployerStateDeposit) -> None:
        """Keep the deployer's fleet files in the service, so the next snapshot carries them."""
        self._call("PUT", "api/backup/deployer-state", deposit.model_dump(mode="json"))

    def snapshot(self) -> bytes:
        """The newest snapshot, sealed to every registered backup key (`control.snapshot`)."""
        return self._request("GET", "api/backup/snapshot").content

    def fetch_subscription(self, sub_token: str, *, document: str = "") -> str:
        """Fetch a subscription the way a client app does (the `/sub` route, no admin token needed)."""
        path = f"sub/{_segment(sub_token)}" + (f"/{document}" if document else "")
        response = self._request("GET", path)
        return response.text

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        return self._request(method, path, body).json()

    def _request(
        self, method: str, path: str, body: Any = None, *, mutation_method: str | None = None
    ) -> httpx.Response:
        activity = mutation_method or method
        try:
            response = self._client.request(method, path, json=body)
        except httpx.RequestError as exc:
            lost = ControlServiceError
            if not isinstance(exc, _NEVER_SENT):
                self._reported(activity)  # the write may have committed before its reply was lost
                lost = self._failure(activity)
            raise lost(
                f"Could not reach the control service: {type(exc).__name__}",
                hint="Check that the control server answers on 443, then retry.",
                category="system",
                retryable=True,
            ) from exc
        if response.status_code == 404:
            raise ControlServiceNotFound(_message(response), category="user")
        if response.status_code == 409:
            if _error_body(response).get("code") == "credentials_changed":
                raise ControlServiceCredentialsChanged(_message(response), category="user")
            if _error_body(response).get("code") == "history_off":
                raise ControlServiceHistoryOff(
                    _message(response),
                    hint="Enable daily history with: meridian privacy set --history daily --yes.",
                    category="user",
                )
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
        if response.status_code >= 500:
            self._reported(activity)  # a gateway timeout or a crash after the commit: perhaps changed
        if response.status_code in _GATEWAY_FAILURES:
            # nginx answers for a stopped or hung service: unreachable, not a refusal.
            raise self._failure(activity)(
                f"The control service did not answer (HTTP {response.status_code} from the control server's proxy).",
                hint="Check the service: meridian check; on the server, systemctl status ctld.",
                category="system",
                retryable=True,
            )
        if response.status_code >= 400:
            raise (self._failure(activity) if response.status_code >= 500 else ControlServiceError)(
                f"Control service error: {_message(response)}",
                category="system" if response.status_code >= 500 else "user",
            )
        self._reported(activity)
        return response

    @staticmethod
    def _failure(method: str) -> type[ControlServiceError]:
        """What a request that may have reached the service raises: a write may have taken effect."""
        return ControlServiceUncertain if method != "GET" else ControlServiceError

    def _reported(self, method: str) -> None:
        """Tell listeners this write changed, or may have changed, the service's state."""
        if method != "GET" and self._report_mutations:
            for listener in list(_mutation_listeners):
                listener(self.url)


def _segment(value: str) -> str:
    return quote(value, safe="")


def _message(response: httpx.Response) -> str:
    return str(_error_body(response).get("message") or f"HTTP {response.status_code}")


def _error_body(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, dict) else {}
