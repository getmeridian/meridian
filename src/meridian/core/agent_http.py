"""The HTTPS long-poll transport of `meridian.agent/v1`, described as data.

`AGENT_HTTP_SURFACE` is exported to `contracts/meridian/v1/agent/http.json`.
It names every endpoint, its method and authentication, and the published
schema of its request and response bodies; it holds no implementation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from meridian.core.agent_protocol import AgentErrorCode
from meridian.core.agent_types import AGENT_PROTOCOL, AgentModel, AgentProtocol

EndpointAuth = Literal["enrollment_token", "node_proof", "session"]


class AgentEndpoint(AgentModel):
    """One HTTP endpoint of the agent transport."""

    name: str = Field(pattern=r"^[a-z][a-z_]*$")
    method: Literal["GET", "POST"]
    path: str = Field(
        pattern=r"^[a-z][a-z0-9/_{}-]*$",
        description="Path below `{service_url}/{base_path}/`; `{name}` segments are parameters.",
    )
    auth: EndpointAuth = Field(
        description=(
            "`enrollment_token`: the body carries the one-time token. `node_proof`: the body carries a signed "
            "`NodeProof`. `session`: `Authorization: Bearer <session_token>` from `welcome`."
        )
    )
    request_schema: str | None = Field(
        description="Agent schema name (`agent/schemas/<name>.schema.json`) of the body; `null` for none."
    )
    response_schema: str | None = Field(description="Agent schema name of a 200 JSON body; `null` if not JSON.")
    response_content_type: Literal["application/json", "application/octet-stream"]
    long_poll: bool
    idempotent: bool = Field(description="Repeating the request with the same body has no further effect.")
    summary: str = Field(max_length=400)


class LongPollSemantics(AgentModel):
    """How `poll` holds a request open."""

    default_wait_seconds: int = Field(ge=1, le=120)
    max_wait_seconds: int = Field(ge=1, le=120)
    client_timeout_margin_seconds: int = Field(
        ge=1, le=60, description="The agent's read timeout is `wait_seconds` plus this margin."
    )
    returns_early_when: list[str] = Field(description="Events that end the wait before `wait_seconds`.")
    empty_result: str = Field(description="What an answer with nothing new means and what the agent does next.")


class AgentErrorMapping(AgentModel):
    """HTTP status per error code; every non-2xx body is the error schema."""

    code: AgentErrorCode
    http_status: int = Field(ge=400, le=599)


class AgentHttpSurface(AgentModel):
    """Every endpoint of the `meridian.agent/v1` HTTPS transport."""

    schema_version: Literal["meridian.agent-http/v1"] = Field(default="meridian.agent-http/v1", alias="schema")
    protocol: AgentProtocol = AGENT_PROTOCOL
    base_path: Literal["agent/v1"] = Field(
        default="agent/v1", description="Joined to the bootstrap `service_url` (scheme, host, secret path)."
    )
    content_type: Literal["application/json"] = "application/json"
    max_request_bytes: int = Field(ge=1, description="Larger bodies get `payload_too_large`.")
    max_response_bytes: int = Field(ge=1, description="Largest JSON body an agent must accept (desired state).")
    transport_notes: list[str]
    long_poll: LongPollSemantics
    error_schema: str
    errors: list[AgentErrorMapping]
    endpoints: list[AgentEndpoint]


def _endpoint(
    name: str,
    path: str,
    auth: EndpointAuth,
    request: str | None,
    response: str | None,
    summary: str,
    *,
    method: Literal["GET", "POST"] = "POST",
    long_poll: bool = False,
    idempotent: bool = True,
) -> AgentEndpoint:
    return AgentEndpoint(
        name=name,
        method=method,
        path=path,
        auth=auth,
        request_schema=request,
        response_schema=response,
        response_content_type="application/json" if response else "application/octet-stream",
        long_poll=long_poll,
        idempotent=idempotent,
        summary=summary,
    )


AGENT_HTTP_SURFACE = AgentHttpSurface(
    max_request_bytes=4 * 1024 * 1024,
    max_response_bytes=32 * 1024 * 1024,
    transport_notes=[
        "HTTPS on the control server's port 443 under its secret path; the TLS handshake never requests a client "
        "certificate, so it is indistinguishable from today's.",
        "Agents dial out and nodes open no ports. A later WebSocket transport carries the same message bodies.",
        "The agent verifies the service's TLS certificate (WebPKI, or `tls_pin_sha256` from bootstrap) and, "
        "independently, the service signature on every desired state and command.",
        "On a network error or a retryable error the agent backs off exponentially with jitter (1 s doubling to "
        "60 s), never below `retry_after_seconds`.",
        "A `session` request answered `unauthenticated` or `credential_expired` sends the agent back to `hello`; "
        "`credential_revoked` stops it until SSH bootstrap enrolls it again.",
    ],
    long_poll=LongPollSemantics(
        default_wait_seconds=25,
        max_wait_seconds=55,
        client_timeout_margin_seconds=10,
        returns_early_when=[
            "the node's (fleet_epoch, revision) is newer than the poll's `have`",
            "a command for the node is waiting",
            "the session is about to expire",
        ],
        empty_result=(
            "200 with `desired` null and no commands: nothing changed within `wait_seconds`. The agent polls again "
            "after `poll_after_seconds` (0 = at once)."
        ),
    ),
    error_schema="agent-error-response",
    errors=[
        AgentErrorMapping(code="invalid_request", http_status=400),
        AgentErrorMapping(code="unsupported_protocol", http_status=400),
        AgentErrorMapping(code="enrollment_token_invalid", http_status=401),
        AgentErrorMapping(code="unauthenticated", http_status=401),
        AgentErrorMapping(code="credential_expired", http_status=401),
        AgentErrorMapping(code="clock_skew", http_status=401),
        AgentErrorMapping(code="credential_revoked", http_status=403),
        AgentErrorMapping(code="not_found", http_status=404),
        AgentErrorMapping(code="conflict", http_status=409),
        AgentErrorMapping(code="epoch_superseded", http_status=409),
        AgentErrorMapping(code="payload_too_large", http_status=413),
        AgentErrorMapping(code="counter_regression", http_status=422),
        AgentErrorMapping(code="rate_limited", http_status=429),
        AgentErrorMapping(code="internal", http_status=500),
        AgentErrorMapping(code="unavailable", http_status=503),
    ],
    endpoints=[
        _endpoint(
            "enroll",
            "enroll",
            "enrollment_token",
            "agent-enroll-request",
            "agent-enroll-response",
            "Trade the one-time token and a CSR for a node certificate. A spent token answers "
            "`enrollment_token_invalid`.",
            idempotent=False,
        ),
        _endpoint(
            "hello",
            "hello",
            "node_proof",
            "agent-hello-request",
            "agent-hello-response",
            "Open a session and negotiate protocol and capabilities. Each hello needs a fresh nonce.",
            idempotent=False,
        ),
        _endpoint(
            "renew",
            "renew",
            "session",
            "agent-renew-request",
            "agent-renew-response",
            "Get a certificate for a freshly generated node key.",
            idempotent=False,
        ),
        _endpoint(
            "poll",
            "poll",
            "session",
            "agent-poll-request",
            "agent-poll-response",
            "Long-poll for a newer whole-node desired state and pending commands.",
            long_poll=True,
        ),
        _endpoint(
            "report",
            "report",
            "session",
            "agent-observed-state-report",
            "agent-report-ack",
            "Report observed per-resource state, health, and versions.",
        ),
        _endpoint(
            "stats",
            "stats",
            "session",
            "agent-stats-report",
            "agent-stats-ack",
            "Send cumulative per-user traffic counters; the ack names the highest counted `seq`.",
        ),
        _endpoint(
            "command_result",
            "command-results",
            "session",
            "agent-command-result",
            "agent-command-result-ack",
            "Report a command's outcome; the service stops redelivering it once acknowledged.",
        ),
        _endpoint(
            "blob",
            "blobs/{sha256}",
            "session",
            None,
            None,
            "Download content by SHA-256 (file content and artifacts with source `service`). The agent verifies "
            "the hash; an unknown hash answers `not_found`.",
            method="GET",
        ),
    ],
)
