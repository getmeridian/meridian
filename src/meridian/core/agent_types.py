"""Shared scalars and small models for the `meridian.agent/v1` protocol.

`meridian.agent/v1` is the language-neutral contract between the control
service (`meridian serve`) and the node agent on every server. The models in
`agent_types`, `agent_desired`, `agent_protocol`, and `agent_http` are the
protocol's documentation: their docstrings and field descriptions become the
published JSON Schema in `contracts/meridian/v1/`, from which the Rust agent
generates its types.

Wire rules that apply to every message:

- JSON, UTF-8, object keys ASCII. No floating-point numbers anywhere; sizes,
  counters, and durations are integers no larger than 2^53 - 1.
- Every field is always sent, `null` included. Unknown fields are rejected, so
  an addition needs a negotiated capability (see `HelloRequest`) or a new
  protocol version.
- Timestamps are RFC 3339 UTC strings ending in `Z`.
- Hashes are lowercase hex SHA-256. A hash of a JSON value is SHA-256 over its
  RFC 8785 (JCS) canonical serialization, computed over the JSON exactly as it
  travelled on the wire, never over a re-serialized typed structure.
- Signatures are Ed25519, base64 (standard alphabet, padded), over the UTF-8
  bytes of a context line (for example `meridian.agent/v1 desired-state`),
  a newline, and the JCS serialization of the signed object with its
  `signature` member removed.
- Privacy: no message from an agent carries a client IP address, a connection
  source, or anything device-identifying. A seized control server must hold no
  record of who connected from where; agents keep such data in memory at most.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, ConfigDict, Field

from meridian.core.inputs import TopologyIdValue
from meridian.core.models import CoreModel

AgentProtocol = Literal["meridian.agent/v1"]
AGENT_PROTOCOL: AgentProtocol = "meridian.agent/v1"

MAX_SAFE_INTEGER = 2**53 - 1
_UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
_BASE64URL_TOKEN_PATTERN = r"^[A-Za-z0-9_-]+$"
_BASE64_PATTERN = r"^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$"


def _calendar_timestamp(value: str) -> str:
    try:
        datetime(
            int(value[:4]),
            int(value[5:7]),
            int(value[8:10]),
            int(value[11:13]),
            int(value[14:16]),
            int(value[17:19]),
        )
    except ValueError as exc:
        raise ValueError("Enter a valid calendar date and time in UTC.") from exc
    return value


class AgentModel(CoreModel):
    """Base for every `meridian.agent/v1` model: strict, frozen, extra fields refused.

    Defaulted fields are still required in the published schema, because both
    peers always send every field.
    """

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


FleetId = Annotated[
    str,
    Field(
        pattern=_UUID_PATTERN,
        description="Fleet identifier: a lowercase UUID fixed when the fleet is created.",
    ),
]
NodeId = Annotated[
    TopologyIdValue,
    Field(description="Node identifier: the server's lowercase topology ID, unique within the fleet."),
]
FleetEpoch = Annotated[
    int,
    Field(
        ge=1,
        le=MAX_SAFE_INTEGER,
        description=(
            "Fleet epoch. A restore of the control service bumps it. An agent remembers the highest epoch it "
            "accepted and refuses desired state and commands from an older one (a resurrected old control server)."
        ),
    ),
]
Revision = Annotated[
    int,
    Field(
        ge=1,
        le=MAX_SAFE_INTEGER,
        description=(
            "Desired-state revision for one node. Ordering is (fleet_epoch, revision): within an epoch revisions only "
            "grow; a higher epoch wins even with a lower revision."
        ),
    ),
]
Sha256Hex = Annotated[
    str,
    Field(pattern=r"^[0-9a-f]{64}$", description="Lowercase hex SHA-256 digest."),
]
Timestamp = Annotated[
    str,
    Field(
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z$",
        max_length=40,
        description="RFC 3339 timestamp in UTC, ending in Z.",
    ),
    AfterValidator(_calendar_timestamp),
]
CommandId = Annotated[
    str,
    Field(
        pattern=_UUID_PATTERN,
        description="Idempotency key of a command: a lowercase UUID chosen by the service, never reused.",
    ),
]
CounterEpoch = Annotated[
    str,
    Field(
        pattern=_UUID_PATTERN,
        description=(
            "Identity of the agent's traffic counter store: a lowercase UUID the agent draws when it creates the "
            "store. It changes only when the store is lost, which restarts every cumulative counter from zero."
        ),
    ),
]
ResourceId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9][a-z0-9._:-]*$",
        description="Resource identifier, unique within one desired-state document and stable across revisions.",
    ),
]
UserId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
        description=(
            "Opaque, stable user identifier assigned by the service. Xray carries it as the client `email`, so "
            "traffic counters map back to it; it is never a person's name."
        ),
    ),
]
InboundTag = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", description="Xray inbound tag."),
]
Version = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9.+_-]*$",
        description="Version string as its upstream publishes it, for example 26.7.11 or v2.9.6.",
    ),
]
Base64 = Annotated[str, Field(pattern=_BASE64_PATTERN, description="Standard base64 with padding.")]
Signature = Annotated[
    str,
    Field(
        min_length=88,
        max_length=88,
        pattern=_BASE64_PATTERN,
        description="Ed25519 signature (64 bytes) in standard base64.",
    ),
]
OpaqueToken = Annotated[
    str,
    Field(min_length=32, max_length=256, pattern=_BASE64URL_TOKEN_PATTERN, description="Opaque base64url token."),
]
CertificatePem = Annotated[
    str,
    Field(
        min_length=64,
        max_length=8192,
        pattern=r"^-----BEGIN CERTIFICATE-----\n[A-Za-z0-9+/=\n]+-----END CERTIFICATE-----\n?$",
        description="One X.509 certificate in PEM.",
    ),
]
CsrPem = Annotated[
    str,
    Field(
        min_length=64,
        max_length=8192,
        pattern=r"^-----BEGIN CERTIFICATE REQUEST-----\n[A-Za-z0-9+/=\n]+-----END CERTIFICATE REQUEST-----\n?$",
        description="PKCS#10 certificate signing request in PEM for an Ed25519 key generated on the node.",
    ),
]
Count = Annotated[int, Field(ge=0, le=MAX_SAFE_INTEGER)]
Seconds = Annotated[int, Field(ge=0, le=86_400, description="Duration in whole seconds.")]
ShortText = Annotated[
    str,
    Field(
        max_length=1000,
        description="Operator-facing text. Never carries secrets, client addresses, or raw command lines.",
    ),
]

Arch = Literal["amd64", "arm64"]


class NodeStateRef(AgentModel):
    """Points at one desired-state document by epoch, revision, and content hash."""

    fleet_epoch: FleetEpoch
    revision: Revision
    sha256: Sha256Hex = Field(description="The document's `sha256` (hash of its `resources`).")


class ServiceSignature(AgentModel):
    """Ed25519 signature by the control service's signing key.

    The key's certificate is issued by the fleet's pinned internal CA and reaches
    the agent in `EnrollResponse` and `HelloResponse`. The agent verifies the
    signature before acting, so a TLS interception or a self-signed fallback
    certificate on 443 can never inject desired state or commands.
    """

    key_id: Sha256Hex = Field(description="SHA-256 of the DER signing certificate that made the signature.")
    signature: Signature


def canonical_json(value: Any) -> bytes:
    """Return the RFC 8785 serialization of protocol JSON (ASCII keys, no floats)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return the protocol hash of a JSON value: SHA-256 over its canonical serialization."""
    return hashlib.sha256(canonical_json(value)).hexdigest()
