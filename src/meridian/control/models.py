"""Models the control service shares with the deployer and the node agent.

The walking skeleton authenticates agents with a per-node bearer token and pins
the service's raw Ed25519 public key; enrollment with a CSR and the fleet's
internal CA (`AgentBootstrap`, `EnrollRequest`) replaces both in M2.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from meridian.core.agent_desired import AgentResource
from meridian.core.agent_types import (
    AGENT_PROTOCOL,
    AgentModel,
    AgentProtocol,
    Base64,
    FleetEpoch,
    FleetId,
    InboundTag,
    NodeId,
    NodeStateRef,
    OpaqueToken,
    Revision,
    Timestamp,
    Version,
)
from meridian.core.models import CoreModel

ServiceUrl = Field(
    max_length=512,
    pattern=r"^https://[^\s/]+/[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*$",
    description="Base URL of the control service: scheme, host, and secret path, without a trailing slash.",
)


class NodeBootstrap(AgentModel):
    """The file SSH bootstrap writes at `/etc/netagent/bootstrap.json` (mode 0600)."""

    protocol: AgentProtocol = AGENT_PROTOCOL
    service_url: str = ServiceUrl
    fleet_id: FleetId
    node_id: NodeId
    node_token: OpaqueToken = Field(description="Sent as `Authorization: Bearer <token>` on every request.")
    service_public_key: Base64 = Field(
        min_length=44, max_length=44, description="The service's raw 32-byte Ed25519 public key in base64."
    )


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


class ClientEndpoint(CoreModel):
    """One public way into an exit, as subscriptions publish it."""

    remark: str = Field(min_length=1, max_length=100)
    protocol: Literal["reality", "xhttp", "hysteria2"]
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


UserStatus = Literal["active", "disabled", "limited", "expired"]
LimitStrategy = Literal["no_reset", "day", "week", "month"]


class UserRecord(CoreModel):
    """One access user as the service stores it.

    The operator sets `active` or `disabled`; the service's enforcement turns an
    active user `expired` once `expire_at` passes and `limited` at the traffic limit.
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


class UserUpdate(CoreModel):
    """A partial change to one user; omitted fields keep their value, `expire_at: null` clears the expiry."""

    status: Literal["active", "disabled"] | None = None
    expire_at: Timestamp | None = None
    traffic_limit_bytes: int | None = Field(default=None, ge=0)
    traffic_limit_strategy: LimitStrategy | None = None


class ServiceSettings(CoreModel):
    profile_title: str = Field(min_length=1, max_length=100)


class ServiceHealth(CoreModel):
    status: Literal["ok"] = "ok"
    version: str
    fleet_id: FleetId
    fleet_epoch: FleetEpoch


class MintedToken(CoreModel):
    node_id: NodeId
    node_token: OpaqueToken


class ServiceConfig(CoreModel):
    """`/etc/ctld/config.json`, written by SSH bootstrap; the admin token is stored only as a hash."""

    fleet_id: FleetId
    admin_token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    listen_host: str = "127.0.0.1"
    listen_port: int = Field(default=3000, ge=1, le=65535)
    state_dir: str = "/var/lib/ctld"
