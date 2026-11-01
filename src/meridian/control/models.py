"""Models of the control service's admin API, shared with the deployer.

The agent side speaks the `meridian.agent/v1` contract (`core/agent_protocol.py`);
these models are what the deployer stores and reads over `/api`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BeforeValidator, Field, field_validator, model_validator

from meridian.core.account_schedule import AccountLifecycleSettings, AccountLifecycleState, AccountSchedulePreview
from meridian.core.agent_desired import AgentResource
from meridian.core.agent_protocol import NodeHealth, NodeVersions
from meridian.core.agent_types import (
    CertificatePem,
    Count,
    FleetEpoch,
    FleetId,
    InboundTag,
    NodeId,
    NodeStateRef,
    OpaqueToken,
    Revision,
    Sha256Hex,
    Timestamp,
    Version,
)
from meridian.core.client_devices import ClientDevicesResult, DeviceCap
from meridian.core.client_limits import MAX_TRAFFIC_LIMIT_BYTES, LimitStrategy, UserCreationSettings
from meridian.core.client_sessions import CredentialGeneration
from meridian.core.connection_pages import ConnectionPageSettings
from meridian.core.defaults import DEFAULT_CLIENT_ROUTING, SUBSCRIPTION_UPDATE_INTERVAL_HOURS
from meridian.core.inputs import ServerReferenceValue, validate_ip_address_value
from meridian.core.languages import Language
from meridian.core.models import CoreModel
from meridian.core.person_activity import PersonAttentionReason
from meridian.core.person_metadata import PersonTag, PrivateNote
from meridian.core.server_allowances import ServerAllowanceSpec
from meridian.core.topology import ClientRoutingPolicy, ProtocolKind, SubscriptionUpdateIntervalHours
from meridian.core.traffic import NodeTrafficAccounting, PeriodTraffic, UserTrafficEvidence


class ManagedInbound(CoreModel):
    """An Xray inbound whose clients the service fills from its user records."""

    tag: InboundTag
    protocol: Literal["vless", "hysteria2"]
    flow: Literal["", "xtls-rprx-vision"] = ""


def users_resource_id(tag: str) -> str:
    """The resource ID of a managed inbound's users, so the Xray unit can restart on it."""
    return f"users:{tag.lower()}"


class NodeSpec(CoreModel):
    """What the deployer asks one node to run; the service adds user resources and signs."""

    resources: list[AgentResource] = Field(max_length=1000)
    managed_inbounds: list[ManagedInbound] = Field(
        default_factory=list,
        max_length=64,
        description="Each gets a users resource, `users_resource_id(tag)`, right after the `xray_config` resource.",
    )


# Agents long-poll for at most 25 s (plus a 10 s grace) and every poll counts as seen,
# so an agent unseen for longer than this has lost the service.
AGENT_SEEN_SECONDS = 90


class NodeRuntime(CoreModel):
    """Runtime evidence from one agent report; polls refresh reachability, never this evidence."""

    health: NodeHealth
    versions: NodeVersions
    observed_at: Timestamp

    def is_fresh(self, now: datetime | None = None) -> bool:
        age = ((now or datetime.now(UTC)) - datetime.fromisoformat(self.observed_at)).total_seconds()
        # The authenticated agent protocol allows up to five minutes of clock skew.
        return -300 <= age < AGENT_SEEN_SECONDS

    @property
    def degraded(self) -> bool:
        return self.health.status != "healthy" or any(check.status != "pass" for check in self.health.checks)


class NodeStatus(NodeTrafficAccounting):
    """The service's view of one node."""

    node_id: NodeId
    fleet_epoch: FleetEpoch
    revision: Revision | None
    sha256: str | None
    spec_sha256: str | None = Field(description="Hash of the last `NodeSpec` the deployer stored.")
    applied: NodeStateRef | None
    last_seen_at: Timestamp | None
    agent_version: Version | None
    failed: list[str] = Field(default_factory=list, description="Resource IDs the last report marked failed.")
    errors: list[str] = Field(default_factory=list, description="Operator-facing messages of failed resources.")
    runtime: NodeRuntime | None = Field(default=None, description="The last report's health and version evidence.")

    def unseen_seconds(self, now: datetime | None = None) -> float | None:
        """Seconds since the agent last reached the service; None if it never did."""
        if self.last_seen_at is None:
            return None
        return ((now or datetime.now(UTC)) - datetime.fromisoformat(self.last_seen_at)).total_seconds()

    def is_live(self, now: datetime | None = None) -> bool:
        unseen = self.unseen_seconds(now)
        return unseen is not None and unseen < AGENT_SEEN_SECONDS

    def runtime_available(self, now: datetime | None = None) -> bool:
        return self.is_live(now) and self.runtime is not None and self.runtime.is_fresh(now)

    @property
    def in_sync(self) -> bool:
        """The agent reported applying exactly the current document: epoch, revision, and hash."""
        return self.applied is not None and (
            self.applied.fleet_epoch,
            self.applied.revision,
            self.applied.sha256,
        ) == (self.fleet_epoch, self.revision, self.sha256)


ENDPOINT_REMARK_MAX_LENGTH = 100


class ClientEndpoint(CoreModel):
    """One public way into an exit, as subscriptions publish it.

    TLS transports (XHTTP, WSS, Hysteria2) carry `tls_sni`; XHTTP and WSS add `host` and
    `path`, and on a domain their `address` is that domain, so they can ride a CDN.
    """

    remark: str = Field(min_length=1, max_length=ENDPOINT_REMARK_MAX_LENGTH)
    protocol: ProtocolKind
    address: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    position: int = Field(ge=0, le=10_000)
    enabled: bool = Field(
        default=True,
        exclude_if=lambda value: value is True,
        description="False omits this endpoint from delivery; reviewed endpoint apply restores topology settings.",
    )
    fingerprint: str = Field(default="", max_length=32)
    reality_sni: str = ""
    reality_public_key: str = ""
    reality_short_id: str = ""
    tls_sni: str = ""
    host: str = ""
    path: str = ""
    salamander_password: str = ""
    fingerprint_copy: bool = Field(
        default=False,
        description="Another fingerprint of an entry listed before it: Base64 and Mihomo list it, failover groups "
        "skip it, Xray JSON gives copies their own Automatic config, and sing-box omits it.",
    )

    @field_validator("address")
    @classmethod
    def bare_ipv6_address(cls, address: str) -> str:
        if "[" not in address and "]" not in address:
            return address
        if not address.startswith("[") or not address.endswith("]") or ":" not in address:
            raise ValueError("Enter a valid IPv6 address, with optional brackets.")
        return validate_ip_address_value(address[1:-1])


UserStatus = Literal["active", "disabled", "limited", "expired"]
DeviceLimitLevel = Literal["off", "private", "full"]
UsageHistoryLevel = Literal["off", "daily"]


class PrivacySettings(CoreModel):
    """Operator-owned privacy choices; reviewed applies never replace them."""

    device_limits: DeviceLimitLevel = "off"
    default_device_limit: int = Field(default=3, ge=1, le=100)
    usage_history: UsageHistoryLevel = "off"
    history_days: int = Field(default=90, ge=7, le=730)


class PrivacyChange(CoreModel):
    id: int | None = Field(default=None, ge=1, description="Local audit row referenced by privacy.changed events.")
    changed_at: Timestamp
    author: str
    before: PrivacySettings
    after: PrivacySettings


class UserDevices(ClientDevicesResult):
    level: DeviceLimitLevel


class UserRecord(UserTrafficEvidence, AccountLifecycleState):
    """One access user as the service stores it.

    The operator sets `active` or `disabled`; the service's enforcement turns an
    active user `expired` once `expire_at` passes and `limited` at the traffic limit,
    and back once a change or a new traffic period lifts it.
    """

    username: str
    user_id: str
    credential_generation: CredentialGeneration = 1
    uuid: str
    sub_token: str
    status: UserStatus
    created_at: Timestamp
    expire_at: Timestamp | None = None
    traffic_limit_bytes: int = Field(default=0, ge=0, le=MAX_TRAFFIC_LIMIT_BYTES, description="0 means unlimited.")
    traffic_limit_strategy: LimitStrategy = "no_reset"
    device_limit: DeviceCap | None = Field(default=None, description="Null uses the fleet's cap; 0 means unlimited.")
    traffic_reset_at: Timestamp | None = Field(default=None, description="When the current traffic period began.")
    used_traffic_bytes: int = Field(default=0, ge=0, description="Traffic since the period began.")
    period_traffic: PeriodTraffic = Field(default_factory=PeriodTraffic)
    lifetime_traffic_bytes: int = Field(
        default=0, ge=0, description="What agents counted plus what an import carried in from before."
    )
    online_at: Timestamp | None = Field(default=None, description="When an agent last counted the user's traffic.")
    never_used: bool = Field(
        default=False,
        description="Derived from lifetime and activity evidence; never stored.",
        json_schema_extra={"readOnly": True},
    )
    quota_used_percent: int | None = Field(
        default=None,
        ge=0,
        description="Exact integer percentage of period quota, rounded down; null when unlimited.",
        json_schema_extra={"readOnly": True},
    )
    attention_reasons: list[PersonAttentionReason] = Field(
        default_factory=list,
        max_length=3,
        description="Derived default-threshold findings at read time; never stored.",
        json_schema_extra={"readOnly": True},
    )
    note: PrivateNote = Field(default="", repr=False, description="Private operator note; people/admin only.")
    tag: PersonTag = Field(default="", repr=False, description="Private operator tag; people/admin only.")
    language: Language | None = Field(
        default=None, description="Starting connection/support page and app status language; null follows the fleet."
    )
    schedule: AccountSchedulePreview | None = Field(
        default=None, description="Read-only UTC dates from the shared calculator."
    )


class UserUpdate(AccountLifecycleSettings):
    """A partial change: omitted fields keep their value; null clears expiry or selects the default device cap."""

    status: Literal["active", "disabled"] | None = None
    expire_at: Timestamp | None = None
    traffic_limit_bytes: int | None = Field(default=None, ge=0, le=MAX_TRAFFIC_LIMIT_BYTES)
    traffic_limit_strategy: LimitStrategy | None = None
    device_limit: DeviceCap | None = None
    note: PrivateNote = Field(default="", repr=False)
    tag: PersonTag = Field(default="", repr=False)
    language: Language | None = Field(
        default=None, description="Page/app language; null follows the fleet, omitted preserves the override."
    )


SUB_TOKEN_PATTERN = r"^[A-Za-z0-9_-]{16,64}$"
# Usernames are never "." or "..": the console builds relative URLs from them, and a browser
# resolves those segments away. Written without lookahead, which Pydantic's regex engine lacks.
USERNAME_PATTERN = r"(?:[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}|\.[A-Za-z0-9_-][A-Za-z0-9_.-]{0,62}|\.\.[A-Za-z0-9_.-]{1,62})"


class UserCreate(UserCreationSettings):
    """A new user with their first settings, created in one step (the console's "Add person")."""

    username: str = Field(pattern=rf"^{USERNAME_PATTERN}$")


class UserPatch(UserUpdate):
    """A partial edit at `PATCH /api/users`, naming the person in the JSON body."""

    username: str = Field(pattern=rf"^{USERNAME_PATTERN}$")

    @property
    def change(self) -> UserUpdate:
        return UserUpdate.model_validate(self.model_dump(include=self.model_fields_set - {"username"}))


class UserImport(AccountLifecycleState):
    """One user carried over from another control plane (`meridian migrate`), credentials and usage included.

    The UUID and subscription token keep every link the user already has working.
    `used_traffic_bytes` is the use of the current traffic period and counts toward
    the limit; `lifetime_traffic_bytes` (never below it) is everything ever counted.
    `traffic_reset_at` is when the source last reset the period, if it knows.
    """

    username: str = Field(pattern=rf"^{USERNAME_PATTERN}$")
    uuid: str = Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
    sub_token: str = Field(pattern=SUB_TOKEN_PATTERN)
    status: UserStatus = "active"
    expire_at: Timestamp | None = None
    traffic_limit_bytes: Count = 0
    traffic_limit_strategy: LimitStrategy = "no_reset"
    used_traffic_bytes: Count = 0
    device_limit: DeviceCap | None = None
    lifetime_traffic_bytes: Count = 0
    traffic_reset_at: Timestamp | None = None
    created_at: Timestamp | None = None
    first_traffic_at: Timestamp | None = None
    note: PrivateNote = Field(default="", repr=False)
    tag: PersonTag = Field(default="", repr=False)


class UserImportBatch(CoreModel):
    """Users imported in one transaction: all of them or none."""

    users: list[UserImport] = Field(max_length=10_000)
    replace: bool = Field(
        default=False,
        description="Overwrite users that exist: the source still serves and changed them since the last import.",
    )

    @model_validator(mode="after")
    def _unique(self) -> UserImportBatch:
        for field_name in ("username", "uuid", "sub_token"):
            values = [str(getattr(user, field_name)).lower() for user in self.users]
            if len(values) != len(set(values)):
                raise ValueError(f"two imported users share a {field_name}")
        return self


class UserImportResult(CoreModel):
    created: list[str] = Field(description="Users the import created.")
    unchanged: list[str] = Field(description="Users that already existed with the same credentials; kept as they are.")
    replaced: list[str] = Field(default_factory=list, description="Users a replacing import overwrote.")


class ServiceSettings(ConnectionPageSettings):
    servers: dict[ServerReferenceValue, ServerAllowanceSpec] = Field(
        default_factory=dict, exclude_if=lambda servers: not servers
    )
    profile_title: str = Field(min_length=1, max_length=100)
    update_interval_hours: SubscriptionUpdateIntervalHours = Field(
        default=SUBSCRIPTION_UPDATE_INTERVAL_HOURS,
        exclude_if=lambda value: value == SUBSCRIPTION_UPDATE_INTERVAL_HOURS,
    )
    client_routing: ClientRoutingPolicy = Field(
        default=DEFAULT_CLIENT_ROUTING, exclude_if=lambda value: value == DEFAULT_CLIENT_ROUTING
    )
    public_url: str = Field(
        default="",
        max_length=512,
        pattern=r"^(?:https://[^\s/]+(?:/[A-Za-z0-9._~-]+)*)?$",
        description="Public base of subscriptions and connection pages (scheme, host, connection-page path), "
        "without a trailing slash; empty leaves out the links built from it.",
    )


class PrivacyState(CoreModel):
    settings: PrivacySettings
    changes: list[PrivacyChange] = Field(max_length=50, description="Newest first, at most 50 changes.")


class PrivacyDeletedRows(CoreModel):
    """Rows removed by a privacy change; lifetime traffic totals are kept."""

    devices: Count = 0
    traffic_daily: Count = 0
    node_traffic_daily: Count = 0


class PrivacyUpdateResult(PrivacyState):
    changed: bool
    deleted: PrivacyDeletedRows = Field(default_factory=PrivacyDeletedRows)
    rekeyed_devices: Count = Field(
        default=0, description="Full device rows converted to fingerprints without metadata."
    )


UtcDay = Annotated[date, BeforeValidator(lambda value: date.fromisoformat(value) if isinstance(value, str) else value)]


class UserTrafficDay(CoreModel):
    day: UtcDay
    uplink_bytes: Count
    downlink_bytes: Count


class UserUsage(CoreModel):
    """UTC days, oldest first, including zero-use days; never reconstructed from lifetime totals."""

    username: str
    days: int = Field(ge=1, le=730, description="Days returned, including today; at most the retention cap.")
    history_days: int = Field(ge=7, le=730)
    daily: list[UserTrafficDay]


class NodeTrafficDay(CoreModel):
    day: UtcDay
    bytes: Count


class NodeDailyUsage(CoreModel):
    node_id: str
    daily: list[NodeTrafficDay]


class TrafficDailySummary(CoreModel):
    """Daily bytes across the fleet and by node, oldest first; includes retained use of removed nodes."""

    days: int = Field(ge=1, le=730)
    history_days: int = Field(ge=7, le=730)
    daily: list[NodeTrafficDay]
    nodes: list[NodeDailyUsage]


BackgroundTaskName = Literal["enforcement", "blob_mirror"]
BackgroundErrorCode = Literal["enforcement_failed", "blob_mirror_failed"]


class BackgroundTaskState(CoreModel):
    """Process-local task evidence; errors are fixed codes, never exception text."""

    name: BackgroundTaskName
    last_attempt_at: Timestamp | None = None
    last_success_at: Timestamp | None = None
    error_code: BackgroundErrorCode | None = None


class ServiceHealth(CoreModel):
    status: Literal["ok", "degraded"] = "ok"
    version: str
    fleet_id: FleetId
    fleet_epoch: FleetEpoch
    background_tasks: list[BackgroundTaskState] = Field(default_factory=list)


class EnrollmentGrant(CoreModel):
    """A single-use, short-lived enrollment token for one node, and the CA the node pins."""

    node_id: NodeId
    enrollment_token: OpaqueToken
    token_expires_at: Timestamp
    ca_sha256: Sha256Hex


class FleetAuthority(CoreModel):
    """The fleet's internal CA, as SSH bootstrap pins it."""

    ca_sha256: Sha256Hex
    ca_certificate_pem: CertificatePem


class UserTrafficTotal(CoreModel):
    """Cumulative traffic of one user over every node; never a client address."""

    username: str
    uplink_bytes: Count
    downlink_bytes: Count
    last_seen_at: Timestamp | None = Field(description="When a node last counted traffic for the user.")


class TrafficSummary(CoreModel):
    users: list[UserTrafficTotal]


TokenScope = Literal["read", "propose", "approve", "people", "admin"]
# People is separate from read < propose < approve; admin includes both capabilities.
TOKEN_SCOPES: tuple[TokenScope, ...] = ("read", "propose", "approve", "people", "admin")
ScopeRequirement: TypeAlias = TokenScope | tuple[TokenScope, ...]


def scope_covers(held: TokenScope, needed: TokenScope) -> bool:
    """Whether a token holding `held` may do what `needed` guards."""
    if held == "admin" or held == needed:
        return True
    if held == "people" or needed == "people":
        return False
    return TOKEN_SCOPES.index(held) >= TOKEN_SCOPES.index(needed)


class TokenCreate(CoreModel):
    """Mint one device's or agent's API token; the scope may not exceed the caller's."""

    name: str = Field(min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$")
    scope: TokenScope
    expires_at: Timestamp | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Optional UTC expiry; use either this or expires_in_days. Null or omitted means never.",
    )
    expires_in_days: int | None = Field(
        default=None,
        ge=1,
        le=3650,
        strict=True,
        exclude_if=lambda value: value is None,
        description="Whole days from issuance, measured by the service clock; null or omitted means never.",
    )

    @model_validator(mode="after")
    def _expiry_choice(self) -> TokenCreate:
        if self.expires_at is not None and self.expires_in_days is not None:
            raise ValueError("Choose either expires_at or expires_in_days.")
        return self


class TokenRecord(CoreModel):
    """One API token's metadata; never its secret, hash, or where it was used from."""

    id: str = Field(pattern=r"^[0-9a-f]{12}$")
    name: str
    scope: TokenScope
    created_at: Timestamp
    last_used_at: Timestamp | None = Field(default=None, description="Coarse: updated at most once a minute.")
    revoked_at: Timestamp | None = None
    expires_at: Timestamp | None = None


class IssuedToken(TokenRecord):
    """A freshly minted token: the only response that ever carries its secret."""

    token: OpaqueToken


class TokenList(CoreModel):
    tokens: list[TokenRecord]


class OwnerStatus(CoreModel):
    """Whether the web UI has an owner yet (`meridian panel` reads it to decide what to print)."""

    exists: bool
    totp_enabled: bool


class SetupLinkRequest(CoreModel):
    """Issue a one-time UI setup link; `reset` also works once an owner exists (a lost password)."""

    reset: bool = False


class SetupLinkGrant(CoreModel):
    """A one-time setup token: valid until `expires_at`, usable once, shown only in this response."""

    token: OpaqueToken
    purpose: Literal["setup", "reset"]
    expires_at: Timestamp


RevisionStatus = Literal["proposed", "approved", "applied", "failed"]
_OPTIONAL_SHA256 = r"^(?:[0-9a-f]{64})?$"


class RevisionActor(CoreModel):
    """Who proposed or approved a revision: an issued token, or the root config token (`token_id` null)."""

    token_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{12}$")
    name: str = Field(description="The token's name; `root` for the configured admin token, the deployer's.")


class RevisionProposal(CoreModel):
    """A reviewed apply about to run: the secret-free topology intent and the hashes the operator approved."""

    intent: dict[str, Any] = Field(description="`topology_intent` as JSON; refused when it carries a secret.")
    plan_hash: Sha256Hex
    review_hash: Sha256Hex

    @model_validator(mode="after")
    def _access_shape(self) -> RevisionProposal:
        """The console reads who the CLI declares from `access`; only its shape is checked, not the whole intent."""
        if "access" not in self.intent:
            return self
        access = self.intent["access"]
        if not isinstance(access, dict):
            raise ValueError("intent.access must be an object")
        users = access.get("users", [])
        if not isinstance(users, list) or not all(isinstance(name, str) for name in users):
            raise ValueError("intent.access.users must be a list of usernames")
        names = access.get("display_names", {})
        if not isinstance(names, dict) or not all(isinstance(name, str) for name in names.values()):
            raise ValueError("intent.access.display_names must map usernames to names")
        return self


class RevisionResult(CoreModel):
    status: Literal["applied", "failed"]
    detail: str = Field(default="", max_length=500)


class RevisionSummary(CoreModel):
    """One reviewed apply: proposed, then approved, then applied or failed."""

    number: int = Field(ge=1, description="Increases by one per proposal and is never reused.")
    created_at: Timestamp
    author: RevisionActor
    intent_sha256: Sha256Hex
    plan_hash: Sha256Hex
    review_hash: Sha256Hex
    status: RevisionStatus
    approved_by: RevisionActor | None = None
    approved_at: Timestamp | None = None
    finished_at: Timestamp | None = None
    detail: str = ""


class RevisionRecord(RevisionSummary):
    intent: dict[str, Any]


class RevisionList(CoreModel):
    revisions: list[RevisionSummary] = Field(description="Newest first.")


class ApplyFenceRecord(CoreModel):
    """The fleet's apply fence (`reconciler/fence.py` decides; the service only compares and swaps)."""

    fleet_id: FleetId
    generation: int = Field(default=0, ge=0)
    plan_hash: str = Field(default="", pattern=_OPTIONAL_SHA256)
    revision: int | None = Field(default=None, ge=1, description="The last applied revision committed here.")
    lease_holder: str = Field(default="", max_length=200)
    lease_expires_at: str = Field(default="", max_length=64)


class ApplyFenceState(CoreModel):
    """The stored fence and its version; version 0 with no record means the service never held it."""

    fleet_id: FleetId = Field(description="The service's own fleet ID.")
    version: int = Field(ge=0)
    record: ApplyFenceRecord | None = None


class ApplyFenceSwap(CoreModel):
    """Replace the fence only if it is still at `expected_version` (409 otherwise)."""

    expected_version: int = Field(ge=0)
    record: ApplyFenceRecord


class ServiceConfig(CoreModel):
    """`/etc/ctld/config.json`, written by SSH bootstrap; the admin token is stored only as a hash."""

    fleet_id: FleetId
    admin_token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    listen_host: str = "127.0.0.1"
    listen_port: int = Field(default=3000, ge=1, le=65535)
    state_dir: str = "/var/lib/ctld"
    public_path: str = Field(
        default="",
        pattern=r"^(?:/[A-Za-z0-9_-]{1,128})?$",
        description="The secret path nginx strips (`/<secret>`): the web owner's session cookie is scoped to it. "
        "Empty keeps the web UI's sign-in closed.",
    )


# -- backups (`backups.py`) -------------------------------------------------------------

AGE_PUBLIC_KEY = r"^age1[02-9ac-hj-np-z]{58}$"


class DeviceKeyCreate(CoreModel):
    """Register a device's backup key: snapshots are sealed to it from the next one on."""

    name: str = Field(min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$")
    public_key: str = Field(pattern=AGE_PUBLIC_KEY, description="The device's X25519 key in age's `age1...` form.")


class DeviceKeyRecord(CoreModel):
    id: str = Field(pattern=r"^[0-9a-f]{12}$")
    name: str
    public_key: str = Field(pattern=AGE_PUBLIC_KEY)
    created_at: Timestamp
    revoked_at: Timestamp | None = None


class DeviceKeyList(CoreModel):
    devices: list[DeviceKeyRecord]


class RecoveryKeyUpdate(CoreModel):
    """Replace the fleet's recovery key; the service only ever holds its public half."""

    public_key: str = Field(pattern=AGE_PUBLIC_KEY)


class RecoveryKeyState(CoreModel):
    public_key: str | None = Field(default=None, pattern=AGE_PUBLIC_KEY)
    set_at: Timestamp | None = None


class DeployerStateDeposit(CoreModel):
    """The deployer's own fleet files, kept so a snapshot alone can rebuild the fleet.

    `cluster_yml` carries no admin token (a restore mints a new one); `servers_json` is the
    server shelf as the deployer saved it.
    """

    cluster_yml: str = Field(min_length=1, max_length=2 * 1024 * 1024)
    servers_json: str = Field(min_length=1, max_length=1024 * 1024)
    generation: int = Field(ge=0, description="The deposited cluster.yml's committed generation.")


class DeployerState(DeployerStateDeposit):
    deposited_at: Timestamp
