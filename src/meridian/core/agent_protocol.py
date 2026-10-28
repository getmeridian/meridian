"""Messages of the `meridian.agent/v1` protocol between the control service and node agents.

Agents dial out; nodes open no ports. Every message is a JSON object with a
`type` discriminator, so a future WebSocket transport can carry the same bodies
the HTTPS long-poll transport (`agent_http`) carries today.

Lifecycle: SSH bootstrap writes an `AgentBootstrap` file; the agent generates
its Ed25519 key on the node and sends `enroll` with a CSR (the private key never
leaves the node); the fleet's pinned internal CA signs it. From then on the
agent opens a session with a signed `hello`, long-polls with `poll` for desired
state and commands, and sends `report`, `stats`, and `command_result`.
Authentication lives inside the application: the 443 TLS handshake never asks
for a client certificate and looks exactly like today's.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field, RootModel, model_validator

from meridian.core.agent_desired import DesiredNodeState, ResourceKind
from meridian.core.agent_types import (
    AGENT_PROTOCOL,
    AgentModel,
    AgentProtocol,
    Arch,
    CertificatePem,
    CommandId,
    Count,
    CounterEpoch,
    CsrPem,
    FleetEpoch,
    FleetId,
    InboundTag,
    NodeId,
    NodeStateRef,
    OpaqueToken,
    ResourceId,
    Revision,
    Seconds,
    ServiceSignature,
    Sha256Hex,
    ShortText,
    Signature,
    Timestamp,
    UserId,
    Version,
)

MAX_TRAFFIC_ENTRIES = 10_000

Capability = Literal[
    "long_poll",
    "files",
    "systemd_units",
    "nginx_artifacts",
    "xray_config",
    "xray_users",
    "hysteria2_users",
    "realm_forwarders",
    "firewall_rules",
    "binary_pins",
    "agent_upgrade",
    "traffic_stats",
    "blob_fetch",
]
CapabilityName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_.]*$",
        description="A capability name. Names the receiver does not know are ignored, never rejected.",
    ),
]

ProtocolName = Annotated[
    str,
    Field(max_length=32, pattern=r"^meridian\.agent/v[1-9][0-9]*$", description="A protocol version name."),
]


class AgentBootstrap(AgentModel):
    """The file SSH bootstrap writes on a node before the agent first starts (mode 0600).

    The enrollment token is single-use and short-lived; the agent deletes it from
    this file once enrolled. `ca_sha256` pins the fleet's internal CA: the agent
    accepts only certificates chaining to that CA.
    """

    protocol: AgentProtocol = AGENT_PROTOCOL
    service_url: str = Field(
        max_length=512,
        pattern=r"^https://[^\s/]+/[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*$",
        description="Base URL of the control service: scheme, host, and secret path, without a trailing slash.",
    )
    fleet_id: FleetId
    node_id: NodeId
    enrollment_token: OpaqueToken
    token_expires_at: Timestamp
    ca_sha256: Sha256Hex = Field(description="SHA-256 of the internal CA certificate (DER).")
    tls_pin_sha256: Sha256Hex | None = Field(
        description="SHA-256 of the service's TLS public key (SPKI) when 443 serves a self-signed certificate."
    )


class EnrollRequest(AgentModel):
    """First contact: trade the one-time enrollment token and a CSR for a node certificate."""

    type: Literal["enroll"] = "enroll"
    protocol: AgentProtocol = AGENT_PROTOCOL
    fleet_id: FleetId
    node_id: NodeId
    enrollment_token: OpaqueToken
    csr_pem: CsrPem
    agent_version: Version
    arch: Arch


class EnrollResponse(AgentModel):
    """The signed node certificate and the service identity the agent pins from now on."""

    type: Literal["enrolled"] = "enrolled"
    protocol: AgentProtocol = AGENT_PROTOCOL
    fleet_id: FleetId
    node_id: NodeId
    fleet_epoch: FleetEpoch
    certificate_pem: CertificatePem = Field(
        description="Node certificate with URI SAN `meridian-agent://<fleet_id>/<node_id>`."
    )
    certificate_expires_at: Timestamp
    ca_certificate_pem: CertificatePem = Field(description="The internal CA; must match the bootstrap `ca_sha256`.")
    service_certificate_pem: CertificatePem = Field(
        description="Certificate of the service signing key that signs desired state and commands."
    )


class NodeProof(AgentModel):
    """Proof of the node key, carried by `hello`.

    The signature covers the whole `hello` body with `proof.signature` removed,
    under the context line `meridian.agent/v1 hello`. The service checks the
    certificate chain, revocation, the SAN against `fleet_id`/`node_id`, that
    `issued_at` is within 300 seconds of its clock, and that `nonce` is unused.
    """

    certificate_pem: CertificatePem
    issued_at: Timestamp
    nonce: OpaqueToken
    signature: Signature


class HelloRequest(AgentModel):
    """Opens a session and negotiates protocol and capabilities."""

    type: Literal["hello"] = "hello"
    protocols: list[ProtocolName] = Field(
        min_length=1,
        max_length=8,
        description="Protocol versions the agent speaks; if none is the service's, it answers `unsupported_protocol`.",
    )
    fleet_id: FleetId
    node_id: NodeId
    agent_version: Version
    arch: Arch
    capabilities: list[CapabilityName] = Field(max_length=64, description="Everything the agent can do.")
    highest_fleet_epoch: FleetEpoch | None = Field(
        description="Highest epoch the agent has accepted; a service on an older epoch answers `epoch_superseded`."
    )
    applied: NodeStateRef | None = Field(description="Last desired state fully applied; `null` before the first.")
    proof: NodeProof


class PollSettings(AgentModel):
    """Timing the service asks the agent to use for this session."""

    max_wait_seconds: int = Field(ge=1, le=120, description="Longest `wait_seconds` the service honours.")
    report_interval_seconds: int = Field(ge=10, le=3600, description="Send `report` at least this often.")
    stats_interval_seconds: int = Field(ge=10, le=3600, description="Send `stats` about this often.")


class HelloResponse(AgentModel):
    """The session: chosen protocol, capabilities the service will use, and a bearer token."""

    type: Literal["welcome"] = "welcome"
    protocol: AgentProtocol
    fleet_epoch: FleetEpoch
    capabilities: list[Capability] = Field(
        max_length=64,
        description=(
            "The subset of the agent's capabilities the service will rely on. It sends no resource kind, command, "
            "or field whose capability is missing here."
        ),
    )
    session_token: OpaqueToken = Field(description="Send as `Authorization: Bearer <token>` on session endpoints.")
    session_expires_at: Timestamp
    server_time: Timestamp
    poll: PollSettings
    renew_after: Timestamp = Field(description="Request a new node certificate (`renew`) after this time.")
    service_certificate_pem: CertificatePem


class RenewRequest(AgentModel):
    """Rotates the node key: a CSR for a fresh Ed25519 key generated on the node."""

    type: Literal["renew"] = "renew"
    csr_pem: CsrPem


class RenewResponse(AgentModel):
    """The new node certificate; the old one stays valid until its own expiry or revocation."""

    type: Literal["renewed"] = "renewed"
    certificate_pem: CertificatePem
    certificate_expires_at: Timestamp


class PollRequest(AgentModel):
    """Long-poll for a newer desired state or pending commands.

    Catch-up is by revision: an agent that was offline sends the document it
    holds and receives the current whole-node document if it differs.
    """

    type: Literal["poll"] = "poll"
    have: NodeStateRef | None = Field(
        description="The newest desired state the agent holds, applied or not; `null` if it holds none."
    )
    wait_seconds: int = Field(ge=0, le=120, description="How long the service may hold the request; 0 answers at once.")


class _CommandBase(AgentModel):
    command_id: CommandId
    fleet_epoch: FleetEpoch
    issued_at: Timestamp
    expires_at: Timestamp = Field(description="The agent answers `expired` instead of running it after this time.")
    signature: ServiceSignature = Field(description="Context line `meridian.agent/v1 command`.")


class ReconcileCommand(_CommandBase):
    """Re-observe every resource now and repair drift."""

    kind: Literal["reconcile"] = "reconcile"


class ReportCommand(_CommandBase):
    """Send `report` now, and `stats` too when asked."""

    kind: Literal["report"] = "report"
    include_stats: bool


class ResetInboundCommand(_CommandBase):
    """Remove and re-add one Xray inbound through gRPC to end open sessions after an urgent cut-off."""

    kind: Literal["reset_inbound"] = "reset_inbound"
    inbound_tag: InboundTag


class RestartUnitCommand(_CommandBase):
    """Restart a systemd unit the desired state manages, named by its resource ID."""

    kind: Literal["restart_unit"] = "restart_unit"
    resource_id: ResourceId


class RenewCredentialCommand(_CommandBase):
    """Run `renew` now, for example after the service rotated the CA's intermediate."""

    kind: Literal["renew_credential"] = "renew_credential"


AgentCommand = Annotated[
    Union[ReconcileCommand, ReportCommand, ResetInboundCommand, RestartUnitCommand, RenewCredentialCommand],
    Field(discriminator="kind"),
]


class AgentCommandSpec(RootModel[AgentCommand]):
    """One command, discriminated by `kind`.

    Commands are idempotent by `command_id`: the service redelivers a command until
    it receives its `command_result`, and the agent remembers results until the
    command expires, answering a redelivery with the remembered result instead of
    running it again. Commands from an epoch below the agent's highest are
    `rejected`.
    """


class PollResponse(AgentModel):
    """Answer to `poll`. Empty (`desired` null, no commands) means nothing changed: poll again."""

    type: Literal["poll_result"] = "poll_result"
    fleet_epoch: FleetEpoch
    desired: DesiredNodeState | None = Field(
        description="The current whole-node document, or `null` when the agent's `have` is current."
    )
    commands: list[AgentCommand] = Field(max_length=64, description="Commands not yet answered, oldest first.")
    poll_after_seconds: Seconds = Field(
        description="Wait this long before the next `poll`; 0 means poll again at once. A load-shedding hint."
    )


ResourceStatus = Literal["pending", "applied", "failed", "drifted", "blocked"]
ResourceErrorCode = Literal[
    "download_failed",
    "hash_mismatch",
    "path_not_allowed",
    "write_failed",
    "disk_full",
    "validation_failed",
    "reload_failed",
    "service_failed",
    "xray_api_failed",
    "rolled_back",
    "timeout",
    "unsupported",
    "internal",
]


class ResourceError(AgentModel):
    """Why a resource or command did not converge."""

    code: ResourceErrorCode
    message: ShortText
    retryable: bool = Field(description="True when the agent will retry on its own.")


class ResourceObservation(AgentModel):
    """Observed state of one resource of the held document.

    `pending` is not yet applied, `applied` matches the desired spec, `failed`
    carries an error, `drifted` was applied but no longer matches on the node,
    and `blocked` waits on a failed dependency.
    """

    id: ResourceId
    kind: ResourceKind
    status: ResourceStatus
    applied_sha256: Sha256Hex | None = Field(
        description="Protocol hash of the resource object last applied successfully; `null` if never applied."
    )
    error: ResourceError | None = Field(description="Set when `status` is `failed`, otherwise `null`.")
    changed_at: Timestamp

    @model_validator(mode="after")
    def _error_iff_failed(self) -> ResourceObservation:
        if (self.error is not None) != (self.status == "failed"):
            raise ValueError("error is required for failed resources and forbidden otherwise")
        return self


class HealthCheck(AgentModel):
    """One local health probe (for example `xray`, `nginx`, `disk`)."""

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_.-]*$")
    status: Literal["pass", "warn", "fail"]
    message: ShortText


class NodeHealth(AgentModel):
    """Aggregate node health. Resource figures are integers to keep the protocol float-free."""

    status: Literal["healthy", "degraded", "unhealthy"]
    checks: list[HealthCheck] = Field(max_length=32)
    uptime_seconds: Count
    load1_milli: Count = Field(description="One-minute load average times 1000.")
    memory_available_bytes: Count
    disk_free_bytes: Count = Field(description="Free bytes on the filesystem holding the agent's state.")


class NodeVersions(AgentModel):
    """Versions of what runs on the node; `null` for software that is not installed."""

    agent: Version
    xray: Version | None
    realm: Version | None
    nginx: Version | None
    os_id: str = Field(max_length=32, pattern=r"^[a-z0-9._-]+$", description="`ID` from /etc/os-release.")
    os_version: str = Field(max_length=32, pattern=r"^[A-Za-z0-9._-]*$", description="`VERSION_ID`.")
    kernel: str = Field(max_length=64, pattern=r"^[A-Za-z0-9._+~-]+$")


class ObservedStateReport(AgentModel):
    """What the node actually runs, sent after every apply and every `report_interval_seconds`."""

    type: Literal["report"] = "report"
    fleet_id: FleetId
    node_id: NodeId
    applied: NodeStateRef | None = Field(description="Last document fully applied; `null` before the first.")
    target: NodeStateRef | None = Field(description="Document being applied now, or `null` when idle.")
    resources: list[ResourceObservation] = Field(max_length=1024, description="One entry per resource of `target`.")
    health: NodeHealth
    versions: NodeVersions
    observed_at: Timestamp


class ReportAck(AgentModel):
    """The service stored the report."""

    type: Literal["report_ack"] = "report_ack"
    applied_revision: Revision | None = Field(description="The applied revision the service recorded.")


class UserTraffic(AgentModel):
    """Cumulative traffic of one user since the counter epoch began. Never decreases."""

    user_id: UserId
    uplink_bytes: Count
    downlink_bytes: Count


class StatsReport(AgentModel):
    """Cumulative per-user traffic counters.

    The agent folds Xray's resettable counters into a durable store, so totals
    survive Xray and agent restarts. Each report carries the totals at one moment
    and the next `seq`. The service keeps the last (counter_epoch, seq, totals)
    per node and counts only the growth since then: a resent or older `seq` is
    acknowledged without counting, so a flaky link never loses or double-counts
    traffic. A new `counter_epoch` starts every user from zero. A user missing
    from a report keeps its stored total; the agent may forget a removed user's
    counter once a report holding its final total is acknowledged.
    """

    type: Literal["stats"] = "stats"
    counter_epoch: CounterEpoch
    seq: int = Field(ge=1, le=2**53 - 1, description="Report number within `counter_epoch`, growing by one.")
    collected_at: Timestamp
    users: list[UserTraffic] = Field(max_length=MAX_TRAFFIC_ENTRIES)

    @model_validator(mode="after")
    def _unique_users(self) -> StatsReport:
        if len({item.user_id for item in self.users}) != len(self.users):
            raise ValueError("user_id values must be unique within a stats report")
        return self


class StatsAck(AgentModel):
    """Every report of `counter_epoch` up to `acked_seq` is durably counted."""

    type: Literal["stats_ack"] = "stats_ack"
    counter_epoch: CounterEpoch
    acked_seq: int = Field(ge=1, le=2**53 - 1)


class CommandResult(AgentModel):
    """Outcome of one command. Sending it again for the same `command_id` is harmless."""

    type: Literal["command_result"] = "command_result"
    command_id: CommandId
    status: Literal["succeeded", "failed", "rejected", "expired"]
    error: ResourceError | None = Field(description="Set when `status` is `failed` or `rejected`.")
    finished_at: Timestamp

    @model_validator(mode="after")
    def _error_matches_status(self) -> CommandResult:
        if (self.error is not None) != (self.status in {"failed", "rejected"}):
            raise ValueError("error is required for failed or rejected commands and forbidden otherwise")
        return self


class CommandResultAck(AgentModel):
    """The service recorded the result and stops redelivering the command."""

    type: Literal["command_result_ack"] = "command_result_ack"
    command_id: CommandId


AgentErrorCode = Literal[
    "invalid_request",
    "unsupported_protocol",
    "enrollment_token_invalid",
    "unauthenticated",
    "credential_expired",
    "credential_revoked",
    "clock_skew",
    "epoch_superseded",
    "counter_regression",
    "conflict",
    "not_found",
    "payload_too_large",
    "rate_limited",
    "unavailable",
    "internal",
]


class AgentError(AgentModel):
    """A refused or failed request."""

    code: AgentErrorCode
    message: ShortText
    retryable: bool
    retry_after_seconds: Seconds | None = Field(
        description="Back off at least this long before retrying; mirrored in the HTTP `Retry-After` header."
    )
    server_time: Timestamp = Field(description="The service clock, so an agent can detect `clock_skew`.")


class AgentErrorResponse(AgentModel):
    """Body of every non-2xx response."""

    type: Literal["error"] = "error"
    protocol: AgentProtocol = AGENT_PROTOCOL
    error: AgentError


AgentMessage = Annotated[
    Union[
        EnrollRequest,
        EnrollResponse,
        HelloRequest,
        HelloResponse,
        RenewRequest,
        RenewResponse,
        PollRequest,
        PollResponse,
        ObservedStateReport,
        ReportAck,
        StatsReport,
        StatsAck,
        CommandResult,
        CommandResultAck,
        AgentErrorResponse,
    ],
    Field(discriminator="type"),
]


class AgentMessageSpec(RootModel[AgentMessage]):
    """Any `meridian.agent/v1` message body, discriminated by `type`."""
