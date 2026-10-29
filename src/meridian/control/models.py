"""Models of the control service's admin API, shared with the deployer.

The agent side speaks the `meridian.agent/v1` contract (`core/agent_protocol.py`);
these models are what the deployer stores and reads over `/api`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from meridian.core.agent_desired import AgentResource
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
from meridian.core.models import CoreModel
from meridian.core.topology import ProtocolKind


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


class NodeStatus(CoreModel):
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

    def unseen_seconds(self, now: datetime | None = None) -> float | None:
        """Seconds since the agent last reached the service; None if it never did."""
        if self.last_seen_at is None:
            return None
        return ((now or datetime.now(UTC)) - datetime.fromisoformat(self.last_seen_at)).total_seconds()

    def is_live(self, now: datetime | None = None) -> bool:
        unseen = self.unseen_seconds(now)
        return unseen is not None and unseen < AGENT_SEEN_SECONDS


class ClientEndpoint(CoreModel):
    """One public way into an exit, as subscriptions publish it.

    TLS transports (XHTTP, WSS, Hysteria2) carry `tls_sni`; XHTTP and WSS add `host` and
    `path`, and on a domain their `address` is that domain, so they can ride a CDN.
    """

    remark: str = Field(min_length=1, max_length=100)
    protocol: ProtocolKind
    address: str = Field(min_length=1, max_length=253)
    port: int = Field(ge=1, le=65535)
    position: int = Field(ge=0, le=10_000)
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


UserStatus = Literal["active", "disabled", "limited", "expired"]
LimitStrategy = Literal["no_reset", "day", "week", "month"]


class UserRecord(CoreModel):
    """One access user as the service stores it.

    The operator sets `active` or `disabled`; the service's enforcement turns an
    active user `expired` once `expire_at` passes and `limited` at the traffic limit,
    and back once a change or a new traffic period lifts it.
    """

    username: str
    user_id: str
    uuid: str
    sub_token: str
    status: UserStatus
    created_at: Timestamp
    expire_at: Timestamp | None = None
    traffic_limit_bytes: int = Field(default=0, ge=0, description="0 means unlimited.")
    traffic_limit_strategy: LimitStrategy = "no_reset"
    traffic_reset_at: Timestamp | None = Field(default=None, description="When the current traffic period began.")
    used_traffic_bytes: int = Field(default=0, ge=0, description="Traffic since the period began.")
    lifetime_traffic_bytes: int = Field(default=0, ge=0)
    online_at: Timestamp | None = Field(default=None, description="When an agent last counted the user's traffic.")


class UserUpdate(CoreModel):
    """A partial change to one user; omitted fields keep their value, `expire_at: null` clears the expiry."""

    status: Literal["active", "disabled"] | None = None
    expire_at: Timestamp | None = None
    traffic_limit_bytes: int | None = Field(default=None, ge=0)
    traffic_limit_strategy: LimitStrategy | None = None


class ServiceSettings(CoreModel):
    profile_title: str = Field(min_length=1, max_length=100)
    public_url: str = Field(
        default="",
        max_length=512,
        pattern=r"^(?:https://[^\s/]+(?:/[A-Za-z0-9._~-]+)*)?$",
        description="Public base of subscriptions and connection pages (scheme, host, connection-page path), "
        "without a trailing slash; empty leaves out the links built from it.",
    )


class ServiceHealth(CoreModel):
    status: Literal["ok"] = "ok"
    version: str
    fleet_id: FleetId
    fleet_epoch: FleetEpoch


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


class ServiceConfig(CoreModel):
    """`/etc/ctld/config.json`, written by SSH bootstrap; the admin token is stored only as a hash."""

    fleet_id: FleetId
    admin_token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    listen_host: str = "127.0.0.1"
    listen_port: int = Field(default=3000, ge=1, le=65535)
    state_dir: str = "/var/lib/ctld"
