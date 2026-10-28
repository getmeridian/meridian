"""Whole-node desired state for `meridian.agent/v1`.

The service sends one `DesiredNodeState` per node. Its `resources` list is
dependency-ordered: every `depends_on` entry names an earlier resource, so
applying the list front to back is always valid. A resource whose dependency
failed is `blocked`, never applied out of order. The agent keeps a ledger of
the resources it applied; one that vanishes from a newer document is retired
(dependents first), so there is no `absent` state on the wire.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
from typing import Annotated, Any, Literal, Union

from pydantic import AfterValidator, Field, RootModel, model_validator

from meridian.core.agent_types import (
    AGENT_PROTOCOL,
    AgentModel,
    AgentProtocol,
    Base64,
    FleetEpoch,
    FleetId,
    InboundTag,
    NodeId,
    ResourceId,
    Revision,
    ServiceSignature,
    Sha256Hex,
    Timestamp,
    UserId,
    Version,
    canonical_sha256,
)
from meridian.core.inputs import HostnameValue, IPAddressValue, PortValue

MAX_RESOURCES = 1024
MAX_USERS_PER_INBOUND = 10_000
MAX_INLINE_FILE_BYTES = 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_BINARY_BYTES = 512 * 1024 * 1024

_SEGMENT = r"[A-Za-z0-9_@+-][A-Za-z0-9._@+-]*"

AbsolutePath = Annotated[
    str,
    Field(
        max_length=255,
        pattern=rf"^(?:/{_SEGMENT})+$",
        description=(
            "Absolute path. No segment may start with a dot, so `.`/`..` traversal and hidden files are impossible. "
            "The agent may still refuse a path outside its allowlist with `path_not_allowed`."
        ),
    ),
]
NginxPath = Annotated[
    str,
    Field(max_length=255, pattern=rf"^/etc/nginx(?:/{_SEGMENT})+$", description="A path below /etc/nginx/."),
]
FileMode = Annotated[str, Field(pattern=r"^[0-7]{4}$", description="Octal permission bits, for example 0644.")]
PosixName = Annotated[
    str,
    Field(min_length=1, max_length=32, pattern=r"^[a-z_][a-z0-9_-]*$", description="POSIX user or group name."),
]
FetchSource = Annotated[
    str,
    Field(
        max_length=2048,
        pattern=r"^(?:service|https://[^\s]+)$",
        description=(
            "Where to download an artifact: an https URL, or `service` for the control service's blob endpoint "
            "(`blobs/{sha256}`). Relays in censored regions may reach only the service, so it is usually last."
        ),
    ),
]


def _validate_cidr(value: str) -> str:
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as exc:
        raise ValueError("Use a network in CIDR form with no host bits set, such as 198.51.100.7/32.") from exc
    return str(network)


ServerCidr = Annotated[
    str,
    Field(max_length=49, pattern=r"^[0-9A-Fa-f:.]+/[0-9]{1,3}$"),
    AfterValidator(_validate_cidr),
]


class _ResourceBase(AgentModel):
    id: ResourceId
    depends_on: list[ResourceId] = Field(
        default_factory=list,
        max_length=64,
        description="Resources that must be applied first; each names an earlier entry of `resources`.",
    )


class FileResource(_ResourceBase):
    """A regular file written atomically (temporary file, fsync, rename)."""

    kind: Literal["file"] = "file"
    path: AbsolutePath
    mode: FileMode
    owner: PosixName
    group: PosixName
    size: int = Field(ge=0, le=MAX_FILE_BYTES, description="Content length in bytes.")
    sha256: Sha256Hex = Field(description="SHA-256 of the file content. The agent verifies it before writing.")
    content_b64: Base64 | None = Field(
        max_length=(MAX_INLINE_FILE_BYTES * 4) // 3 + 4,
        description=("Inline content in base64, at most 1 MiB decoded. `null` means fetch it from `blobs/{sha256}`."),
    )
    sensitive: bool = Field(
        description="Holds key material: the agent never logs or echoes the content and writes it with `mode` only."
    )

    @model_validator(mode="after")
    def _inline_content_matches(self) -> FileResource:
        if self.content_b64 is None:
            return self
        try:
            content = base64.b64decode(self.content_b64, validate=True)
        except binascii.Error as exc:
            raise ValueError("content_b64 is not valid base64") from exc
        if len(content) != self.size or hashlib.sha256(content).hexdigest() != self.sha256:
            raise ValueError("content_b64 does not match size and sha256")
        return self


class SystemdUnitResource(_ResourceBase):
    """A systemd unit file plus its enablement and run state."""

    kind: Literal["systemd_unit"] = "systemd_unit"
    name: str = Field(
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9:_.@-]*\.(?:service|timer|socket|path|target)$",
        description="Unit name, written to /etc/systemd/system/<name>.",
    )
    content: str = Field(min_length=1, max_length=65_536, description="Unit file text.")
    enabled: bool
    state: Literal["started", "stopped"]
    restart_on: list[ResourceId] = Field(
        default_factory=list,
        max_length=64,
        description="Restart the unit when any of these resources changed in the same apply; a subset of `depends_on`.",
    )

    @model_validator(mode="after")
    def _restart_on_is_a_dependency(self) -> SystemdUnitResource:
        if not set(self.restart_on) <= set(self.depends_on):
            raise ValueError("restart_on may name only resources listed in depends_on")
        return self


class NginxFile(AgentModel):
    """One nginx configuration file inside an atomic artifact."""

    path: NginxPath
    content: str = Field(max_length=262_144, description="File text.")
    sha256: Sha256Hex = Field(description="SHA-256 of `content` encoded as UTF-8.")

    @model_validator(mode="after")
    def _hash_matches(self) -> NginxFile:
        if hashlib.sha256(self.content.encode("utf-8")).hexdigest() != self.sha256:
            raise ValueError("sha256 does not match content")
        return self


class NginxArtifactResource(_ResourceBase):
    """A set of nginx files applied as one unit.

    The agent writes every file, runs `nginx -t`, and reloads. If the test or the
    reload fails it restores the previous files, reloads again, and reports the
    resource `failed` with `validation_failed` or `reload_failed`.
    """

    kind: Literal["nginx_artifact"] = "nginx_artifact"
    files: list[NginxFile] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def _unique_paths(self) -> NginxArtifactResource:
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("nginx artifact file paths must be unique")
        return self


def _reject_floats(value: Any) -> None:
    if isinstance(value, float):
        raise ValueError("Xray config must not contain floating-point numbers")
    if isinstance(value, dict):
        for item in value.values():
            _reject_floats(item)
    elif isinstance(value, list):
        for item in value:
            _reject_floats(item)


class XrayConfigResource(_ResourceBase):
    """The Xray configuration, validated with `xray run -test` before it replaces the old file.

    Inbounds whose users come from `xray_users` or `hysteria2_users` resources keep
    an empty `settings.clients` list here. The agent adds users through Xray's gRPC
    API and also writes them into those `clients` lists on disk (without a restart),
    so a reboot never loses a user. A change to this resource restarts Xray through
    the systemd unit that lists it in `restart_on`.
    """

    kind: Literal["xray_config"] = "xray_config"
    path: AbsolutePath
    config: dict[str, Any] = Field(description="Xray JSON configuration object, without managed users.")
    api_address: str = Field(
        pattern=r"^127\.0\.0\.1:[0-9]{1,5}$",
        description="Loopback address of the Xray gRPC API inbound declared in `config`.",
    )

    @model_validator(mode="after")
    def _no_floats(self) -> XrayConfigResource:
        _reject_floats(self.config)
        return self


class VlessUser(AgentModel):
    """A VLESS client of one inbound."""

    user_id: UserId
    uuid: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        description="The client's VLESS UUID (its credential), lowercase.",
    )
    flow: Literal["", "xtls-rprx-vision"] = Field(description="VLESS flow; empty for XHTTP and WebSocket inbounds.")


class Hysteria2User(AgentModel):
    """A Hysteria2 client of one inbound."""

    user_id: UserId
    auth: str = Field(
        min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$", description="The client's Hysteria2 password."
    )


def _unique_users(users: list[VlessUser] | list[Hysteria2User]) -> None:
    ids = [user.user_id for user in users]
    if len(ids) != len(set(ids)):
        raise ValueError("user_id values must be unique within an inbound")


class XrayUsersResource(_ResourceBase):
    """The complete VLESS client list of one Xray inbound, applied through gRPC and persisted to disk."""

    kind: Literal["xray_users"] = "xray_users"
    inbound_tag: InboundTag
    users: list[VlessUser] = Field(max_length=MAX_USERS_PER_INBOUND)

    @model_validator(mode="after")
    def _unique(self) -> XrayUsersResource:
        _unique_users(self.users)
        if len({user.uuid for user in self.users}) != len(self.users):
            raise ValueError("uuid values must be unique within an inbound")
        return self


class Hysteria2UsersResource(_ResourceBase):
    """The complete client list of one Hysteria2 inbound (served by Xray), applied like `xray_users`."""

    kind: Literal["hysteria2_users"] = "hysteria2_users"
    inbound_tag: InboundTag
    users: list[Hysteria2User] = Field(max_length=MAX_USERS_PER_INBOUND)

    @model_validator(mode="after")
    def _unique(self) -> Hysteria2UsersResource:
        _unique_users(self.users)
        return self


class RealmForwarderResource(_ResourceBase):
    """A Realm L4 TCP forwarder, run as an agent-named systemd unit.

    Addresses here are servers of the fleet, never clients. Realm's own logs stay
    on the node and the agent never reports connection sources.
    """

    kind: Literal["realm_forwarder"] = "realm_forwarder"
    listen_address: IPAddressValue
    listen_port: PortValue
    remote_host: IPAddressValue | HostnameValue = Field(description="The next hop: an exit or relay server.")
    remote_port: PortValue


class FirewallRuleResource(_ResourceBase):
    """One inbound firewall allowance. Only compiled rules open public ports."""

    kind: Literal["firewall_rule"] = "firewall_rule"
    port: PortValue
    protocol: Literal["tcp", "udp"]
    allow_from: list[ServerCidr] = Field(
        max_length=64,
        description="Server networks allowed to connect (for source-restricted relay hops); empty allows anyone.",
    )


class BinaryPinResource(_ResourceBase):
    """A pinned upstream binary or data file installed only after its SHA-256 matches."""

    kind: Literal["binary_pin"] = "binary_pin"
    name: Literal["xray", "realm", "geoip", "geosite"]
    version: Version
    sources: list[FetchSource] = Field(min_length=1, max_length=4, description="Tried in order.")
    size: int = Field(ge=1, le=MAX_BINARY_BYTES, description="Download size in bytes.")
    sha256: Sha256Hex = Field(description="SHA-256 of the downloaded artifact (the archive, when there is one).")
    archive: Literal["none", "zip", "tar.gz"]
    member: str | None = Field(
        max_length=255,
        pattern=rf"^{_SEGMENT}(?:/{_SEGMENT})*$",
        description="Path of the installed file inside the archive; `null` exactly when `archive` is `none`.",
    )
    install_path: AbsolutePath
    mode: FileMode

    @model_validator(mode="after")
    def _member_matches_archive(self) -> BinaryPinResource:
        if (self.member is None) != (self.archive == "none"):
            raise ValueError("member is required for an archive and forbidden otherwise")
        return self


class AgentUpgradeResource(_ResourceBase):
    """A staged self-upgrade of the agent with automatic rollback.

    The agent downloads and verifies the new binary, keeps the running one as the
    previous version, swaps, and restarts. The new agent must complete `hello` and
    report this resource `applied` within `health_timeout_seconds`; otherwise the
    previous binary is restored and the resource reports `failed` / `rolled_back`.
    """

    kind: Literal["agent_upgrade"] = "agent_upgrade"
    version: Version
    sources: list[FetchSource] = Field(min_length=1, max_length=4, description="Tried in order.")
    size: int = Field(ge=1, le=MAX_BINARY_BYTES)
    sha256: Sha256Hex
    health_timeout_seconds: int = Field(ge=30, le=900)


AgentResource = Annotated[
    Union[
        FileResource,
        SystemdUnitResource,
        NginxArtifactResource,
        XrayConfigResource,
        XrayUsersResource,
        Hysteria2UsersResource,
        RealmForwarderResource,
        FirewallRuleResource,
        BinaryPinResource,
        AgentUpgradeResource,
    ],
    Field(discriminator="kind"),
]

ResourceKind = Literal[
    "file",
    "systemd_unit",
    "nginx_artifact",
    "xray_config",
    "xray_users",
    "hysteria2_users",
    "realm_forwarder",
    "firewall_rule",
    "binary_pin",
    "agent_upgrade",
]


class AgentResourceSpec(RootModel[AgentResource]):
    """One desired-state resource, discriminated by `kind`."""


def _written_paths(resource: AgentResource) -> list[str]:
    if isinstance(resource, FileResource | XrayConfigResource):
        return [resource.path]
    if isinstance(resource, BinaryPinResource):
        return [resource.install_path]
    if isinstance(resource, NginxArtifactResource):
        return [item.path for item in resource.files]
    return []


def _check_managed_inbounds(resources: list[AgentResource]) -> None:
    managed = [item for item in resources if isinstance(item, XrayUsersResource | Hysteria2UsersResource)]
    if not managed:
        return
    configs = [item for item in resources if isinstance(item, XrayConfigResource)]
    inbounds = configs[0].config.get("inbounds", []) if configs else []
    by_tag = (
        {item.get("tag"): item for item in inbounds if isinstance(item, dict)} if isinstance(inbounds, list) else {}
    )
    tags = [item.inbound_tag for item in managed]
    if len(tags) != len(set(tags)):
        raise ValueError("each inbound has at most one users resource")
    for tag in tags:
        inbound = by_tag.get(tag)
        if inbound is None:
            raise ValueError(f"users resource names inbound {tag!r}, which xray_config does not declare")
        settings = inbound.get("settings")
        if isinstance(settings, dict) and settings.get("clients"):
            raise ValueError(f"inbound {tag!r} must leave settings.clients empty; users resources own them")


class DesiredNodeState(AgentModel):
    """Everything one node should run, as one signed document.

    The agent refuses a document whose `fleet_epoch` is below the highest it has
    accepted, whose (fleet_epoch, revision) is below the one it holds, or that
    repeats a held (fleet_epoch, revision) with a different `sha256`. Equal
    `sha256` means equal content, so a new revision with the same hash only
    records the new revision.
    """

    protocol: AgentProtocol = AGENT_PROTOCOL
    fleet_id: FleetId
    node_id: NodeId
    fleet_epoch: FleetEpoch
    revision: Revision
    sha256: Sha256Hex = Field(description="Protocol hash of the `resources` array as sent (JCS, SHA-256).")
    issued_at: Timestamp
    resources: list[AgentResource] = Field(max_length=MAX_RESOURCES)
    signature: ServiceSignature = Field(description="Context line `meridian.agent/v1 desired-state`.")

    @model_validator(mode="after")
    def _consistent(self) -> DesiredNodeState:
        seen: set[str] = set()
        for resource in self.resources:
            if resource.id in seen:
                raise ValueError(f"duplicate resource id {resource.id!r}")
            missing = [dep for dep in resource.depends_on if dep not in seen]
            if missing:
                raise ValueError(
                    f"resource {resource.id!r} depends on {missing[0]!r}, which is not an earlier resource"
                )
            seen.add(resource.id)
        for kind in ("xray_config", "agent_upgrade"):
            if sum(1 for item in self.resources if item.kind == kind) > 1:
                raise ValueError(f"a node has at most one {kind} resource")
        paths = [path for item in self.resources for path in _written_paths(item)]
        if len(paths) != len(set(paths)):
            raise ValueError("two resources write the same path")
        _check_managed_inbounds(self.resources)
        resources_json = [item.model_dump(mode="json", by_alias=True) for item in self.resources]
        if canonical_sha256(resources_json) != self.sha256:
            raise ValueError("sha256 does not match resources")
        return self
