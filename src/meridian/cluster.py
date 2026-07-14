"""Fleet configuration data model.

Uses a single cluster-wide manifest instead of per-server credential files.
Users/clients live in Remnawave's PostgreSQL — this file stores only
deployment topology and panel access.

YAML persistence (load/save/serialize) lives in ``cluster_persistence.py``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Self

from meridian.core.inputs import validate_hostname_value, validate_optional_transport_path_value
from meridian.core.topology import SetupIntent

logger = logging.getLogger("meridian.cluster")


def _load_warning(message: str, *, details: list[str] | None = None) -> None:
    """Record cluster load warnings without importing CLI renderers."""
    if details:
        logger.warning("%s %s", message, " ".join(details))
    else:
        logger.warning("%s", message)


CURRENT_CLUSTER_VERSION = 3


class ClusterConfigExternallyModifiedError(RuntimeError):
    """cluster.yml was modified on disk between load() and save().

    Raised so callers don't silently clobber the user's (or another
    process's) writes. Most callers should surface the error and ask
    the user to retry after reloading.
    """


class ProtocolKey(StrEnum):
    """Protocol identifiers — single source of truth for string keys."""

    REALITY = "reality"
    XHTTP = "xhttp"
    WSS = "wss"
    HYSTERIA2 = "hysteria2"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PanelConfig:
    """Remnawave panel connection details."""

    url: str = ""  # Panel HTTPS endpoint (e.g., https://panel.example.com)
    api_token: str = field(default="", repr=False)  # Remnawave JWT API token (long-lived)
    admin_user: str = ""  # Panel admin username (for recovery login)
    admin_pass: str = field(default="", repr=False)  # Panel admin password (for recovery login)
    server_ip: str = ""  # IP where panel is deployed
    ssh_user: str = "root"
    ssh_port: int = 22
    secret_path: str = ""  # nginx reverse proxy path to panel
    sub_path: str = ""  # subscription endpoint path
    deployed_with: str = ""  # Meridian CLI version
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def display_url(self) -> str:
        """Panel URL with trailing slash for browser compatibility."""
        if not self.url:
            return ""
        return self.url.rstrip("/") + "/"


@dataclass
class NodeEntry:
    """A proxy node running Remnawave node + Xray."""

    ip: str = ""
    uuid: str = ""  # Remnawave node UUID
    name: str = ""  # friendly name (e.g., "finland")
    ssh_user: str = "root"
    ssh_port: int = 22
    sni: str = ""  # Reality SNI target
    domain: str = ""  # optional domain for WSS/XHTTP
    is_panel_host: bool = False  # panel runs on this node too
    deployed_with: str = ""  # Meridian CLI version
    xhttp_path: str = ""  # persisted XHTTP path (reused across redeploys)
    ws_path: str = ""  # persisted WebSocket path (reused across redeploys)
    reality_public_key: str = ""  # Reality public key (for test command)
    reality_short_id: str = ""  # Reality short ID
    reality_private_key: str = field(default="", repr=False)  # Reality private key (for redeploy config rebuild)
    warp: bool = False  # Cloudflare WARP outbound enabled
    hysteria2: bool = True  # Hysteria2 UDP/443 fallback enabled
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class RelayEntry:
    """A Realm TCP relay forwarding traffic to an exit node."""

    ip: str = ""
    name: str = ""  # friendly name (e.g., "ru-moscow")
    port: int = 443  # relay listen port
    exit_node_ip: str = ""  # which node this relay forwards to
    host_uuids: dict[str, str] = field(default_factory=dict)  # protocol key → Remnawave host UUID
    sni: str = ""  # relay-specific SNI target
    ssh_user: str = "root"
    ssh_port: int = 22
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class BrandingConfig:
    """Server branding for connection pages."""

    server_name: str = ""  # display name (e.g., "Alice's VPN")
    icon: str = ""  # emoji or data URI
    color: str = ""  # palette name (ocean, sunset, forest, lavender, rose, slate)
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class InboundRef:
    """Cached reference to a Remnawave inbound."""

    uuid: str = ""
    tag: str = ""
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class SubscriptionPageConfig:
    """Remnawave subscription page deployment config."""

    enabled: bool = True
    path: str = ""  # nginx proxy path (random hex, generated on first deploy)
    deployed: bool = False  # whether the container has been deployed
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class TelegramConfig:
    """Optional Telegram bot notifications — passed through to Remnawave backend.

    All fields are optional. When ``bot_token`` is set, notifications are
    enabled in the panel's .env on next deploy/redeploy. Chat IDs use the
    ``chat_id`` or ``chat_id:thread_id`` format per Remnawave docs.
    """

    bot_token: str = field(default="", repr=False)
    notify_users: str = ""  # chat_id or chat_id:thread_id
    notify_nodes: str = ""
    notify_crm: str = ""
    notify_service: str = ""
    notify_tblocker: str = ""
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class DesiredNode:
    """A node that should exist in the fleet (desired state for plan/apply).

    ``warp`` uses ``None`` as "not specified — keep whatever the server has".
    ``False`` means "explicitly disable WARP". This matches the name/sni/domain
    sentinel pattern and prevents a desired node that omits warp from
    spuriously triggering an UPDATE_NODE that disables WARP on every apply.
    """

    host: str = ""  # IP address
    name: str = ""  # friendly name (e.g., "de-fra-1")
    ssh_user: str = "root"
    ssh_port: int = 22
    domain: str = ""  # optional domain for WSS/XHTTP
    sni: str = ""  # Reality SNI target
    warp: bool | None = None  # None = keep current; False = explicitly disable
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class DesiredRelay:
    """A relay that should exist in the fleet (desired state for plan/apply)."""

    host: str = ""  # IP address
    name: str = ""  # friendly name (e.g., "relay-msk")
    exit_node: str = ""  # name or IP of the exit node
    sni: str = ""  # camouflage target (empty = use DEFAULT_SNI)
    ssh_user: str = "root"
    ssh_port: int = 22
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class AppliedState:
    """Reconciler snapshot of the last successfully applied desired state.

    Tracks which resources were present in the desired declaration at the
    time of the last successful ``meridian apply``. This lets ``compute_plan``
    distinguish intentional removal (resource was in applied, user removed
    it from desired) from drift (resource exists in panel but was never in
    desired).

    Semantics of None vs empty list:
    - ``None``  = no history (first apply, or category unmanaged). compute_plan
      treats every actual-not-desired resource as drift (conservative).
    - ``[]``    = managed and converged to zero. compute_plan treats
      actual-not-desired resources as intentional removals.
    """

    nodes: list[str] | None = None  # applied node host IPs
    clients: list[str] | None = None  # applied client usernames
    relays: list[str] | None = None  # applied relay host IPs


@dataclass
class RealityKeyBinding:
    """Atomic Reality key material retained for one workload server."""

    public_key: str = ""
    private_key: str = field(default="", repr=False)
    short_id: str = ""
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class WorkloadBinding:
    """Generation-scoped remote bindings for one effective Xray workload."""

    id: str = ""
    kind: Literal["exit", "routing_gateway"] = "exit"
    generation: int = 0
    active: bool = True
    server_refs: list[str] = field(default_factory=list)
    config_profile_uuid: str = ""
    config_profile_name: str = ""
    node_uuids: dict[str, str] = field(default_factory=dict)
    inbound_uuids: dict[str, str] = field(default_factory=dict)
    host_uuids: dict[str, str] = field(default_factory=dict)
    reality_keys: dict[str, RealityKeyBinding] = field(default_factory=dict, repr=False)
    desired_hash: str = ""
    adopted: bool = False
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class ManagedResourceBinding:
    """Observed remote identity for one compiled logical resource generation."""

    logical_id: str = ""
    resource_kind: str = ""
    generation: int = 0
    remote_id: str = ""
    desired_hash: str = ""
    observed_hash: str = ""
    active: bool = False
    adopted: bool = False
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class ResourceAllocation:
    """Stable compiler allocation persisted across observation and apply."""

    logical_id: str = ""
    server_ref: str = ""
    transport: Literal["tcp", "udp", "none"] = "none"
    port: int = 0
    path: str = ""
    tag: str = ""
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)


ActionCheckpointStatus = Literal["pending", "running", "unknown", "succeeded", "failed", "skipped"]


@dataclass
class ActionCheckpoint:
    """Compact durable checkpoint for one immutable resource action."""

    idempotency_key: str = ""
    resource_id: str = ""
    expected_hash: str = ""
    generation: int = 0
    status: ActionCheckpointStatus = "pending"
    attempts: int = 0
    observed_hash: str = ""
    remote_id: str = ""
    last_error: str = ""
    updated_at: str = ""
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class ClusterConfig:
    """Fleet-wide configuration — the sole local state for Meridian 4.0.

    Stored at ~/.meridian/cluster.yml. One file replaces per-server proxy.yml.
    Client/user state lives in Remnawave's database, not here.
    """

    version: int = CURRENT_CLUSTER_VERSION
    panel: PanelConfig = field(default_factory=PanelConfig)
    config_profile_uuid: str = ""
    config_profile_name: str = ""
    squad_uuid: str = ""  # Remnawave internal squad for user-inbound access control
    nodes: list[NodeEntry] = field(default_factory=list)
    relays: list[RelayEntry] = field(default_factory=list)
    branding: BrandingConfig = field(default_factory=BrandingConfig)
    inbounds: dict[str, InboundRef] = field(default_factory=dict)
    # v2: subscription page config (None = not declared, don't manage)
    subscription_page: SubscriptionPageConfig | None = None
    # v2: Telegram notifications (None = not configured, disable in panel)
    telegram: TelegramConfig | None = None
    # v2: desired state for declarative plan/apply workflow
    # None = not declared (don't manage). [] = declared empty (manage, want zero).
    desired_nodes: list[DesiredNode] | None = None
    desired_clients: list[str] | None = None
    desired_relays: list[DesiredRelay] | None = None
    # v2: reconciler applied-state snapshot (last successful apply)
    applied_state: AppliedState = field(default_factory=AppliedState)
    # v3: workload-scoped topology and durable compiled-resource state.
    topology_intent: SetupIntent | None = None
    workloads: list[WorkloadBinding] = field(default_factory=list)
    managed_bindings: dict[str, ManagedResourceBinding] = field(default_factory=dict)
    allocations: dict[str, ResourceAllocation] = field(default_factory=dict)
    action_checkpoints: dict[str, ActionCheckpoint] = field(default_factory=dict)
    active_generation: int = 0
    active_plan_hash: str = ""
    pending_generation: int = 0
    pending_plan_hash: str = ""
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)
    _readonly: bool = field(default=False, repr=False)
    _lock: Any = field(default=None, repr=False)  # threading.RLock for parallel save safety
    # mtime_ns of cluster.yml at load time — used to detect external edits
    # during a long-running apply (save() refuses to clobber if file changed).
    _loaded_mtime_ns: int | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        import threading

        if self._lock is None:
            # RLock (reentrant) so handlers that mutate cluster + save() in
            # one locked region don't deadlock against save()'s own lock.
            self._lock = threading.RLock()

    # --- Persistence ---

    @classmethod
    def load(cls, path: Path | None = None) -> ClusterConfig:
        """Load from cluster.yml. Returns empty config if file doesn't exist."""
        from meridian.cluster_persistence import load_cluster

        return load_cluster(path)

    def save(self, path: Path | None = None) -> None:
        """Write to cluster.yml (atomic via tempfile+rename).

        Thread-safe: acquires an internal lock to prevent concurrent
        saves from parallel node provisioning.
        """
        from meridian.cluster_persistence import save_cluster

        save_cluster(self, path)

    def clone(self) -> Self:
        """Return an isolated in-memory copy with an independent save lock."""
        import copy
        import threading

        return copy.deepcopy(
            self,
            {id(self._lock): threading.RLock()},
        )

    # --- Convenience ---

    def validate(self) -> list[str]:
        """Validate cluster config consistency. Returns list of error strings (empty = valid)."""
        errors: list[str] = []
        if self.is_configured:
            if not self.panel.url:
                errors.append("panel.url is empty but cluster is marked as configured")
            if not self.panel.api_token:
                errors.append("panel.api_token is empty but cluster is marked as configured")

        # Panel server_ip
        if self.panel.server_ip and not _is_valid_ip(self.panel.server_ip):
            errors.append(f"panel.server_ip is not a valid IP: {self.panel.server_ip}")

        # Panel ssh_port
        if not _is_valid_port(self.panel.ssh_port):
            errors.append(f"panel.ssh_port is out of range: {self.panel.ssh_port}")

        # Node validations
        node_ips: list[str] = []
        node_names: list[str] = []
        for i, node in enumerate(self.nodes):
            label = f"nodes[{i}]"
            if node.ip:
                if not _is_valid_ip(node.ip):
                    errors.append(f"{label}.ip is not a valid IP: {node.ip}")
                if node.ip in node_ips:
                    errors.append(f"{label}.ip is a duplicate: {node.ip}")
                node_ips.append(node.ip)
            if node.name:
                # Duplicate node names silently misroute relay exit_node lookups —
                # compute_plan uses a last-wins name→IP map while cluster.find_node
                # returns first match. Disallow at validation time.
                if node.name in node_names:
                    errors.append(f"{label}.name is a duplicate: {node.name}")
                node_names.append(node.name)
            if node.uuid and not _is_valid_uuid(node.uuid):
                errors.append(f"{label}.uuid is not a valid UUID: {node.uuid}")
            if not _is_valid_port(node.ssh_port):
                errors.append(f"{label}.ssh_port is out of range: {node.ssh_port}")
            _append_hostname_error(errors, f"{label}.sni", node.sni)
            _append_hostname_error(errors, f"{label}.domain", node.domain)
            _append_path_error(errors, f"{label}.xhttp_path", node.xhttp_path)
            _append_path_error(errors, f"{label}.ws_path", node.ws_path)

        # Relay validations
        relay_endpoints: set[tuple[str, int]] = set()
        relay_labels: set[str] = set()
        for i, relay in enumerate(self.relays):
            label = f"relays[{i}]"
            if relay.ip and not _is_valid_ip(relay.ip):
                errors.append(f"{label}.ip is not a valid IP: {relay.ip}")
            if not _is_valid_port(relay.port):
                errors.append(f"{label}.port is out of range: {relay.port}")
            if not _is_valid_port(relay.ssh_port):
                errors.append(f"{label}.ssh_port is out of range: {relay.ssh_port}")
            _append_hostname_error(errors, f"{label}.sni", relay.sni)
            if relay.exit_node_ip and node_ips and relay.exit_node_ip not in node_ips:
                errors.append(f"{label}.exit_node_ip references unknown node: {relay.exit_node_ip}")
            # Relay endpoint uniqueness
            if relay.ip:
                endpoint = (relay.ip, relay.port)
                if endpoint in relay_endpoints:
                    errors.append(f"{label}: duplicate relay endpoint {relay.ip}:{relay.port}")
                relay_endpoints.add(endpoint)
            relay_label = _relay_label_value(relay.name, relay.ip)
            if relay_label:
                if relay_label in relay_labels:
                    errors.append(f"{label}.name creates a duplicate relay identity: {relay_label}")
                relay_labels.add(relay_label)

        # Panel host uniqueness — at most one node can be panel host
        panel_hosts = [i for i, n in enumerate(self.nodes) if n.is_panel_host]
        if len(panel_hosts) > 1:
            errors.append(f"Multiple panel hosts detected: nodes[{panel_hosts[0]}] and nodes[{panel_hosts[1]}]")

        # Panel URL format
        if self.panel.url:
            from urllib.parse import urlparse

            parsed = urlparse(self.panel.url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                errors.append(f"panel.url is not a valid HTTP(S) URL: {self.panel.url}")

        # Inbound ref UUIDs
        for key, ref in self.inbounds.items():
            if hasattr(ref, "uuid") and ref.uuid and not _is_valid_uuid(ref.uuid):
                errors.append(f"inbounds[{key}].uuid is not a valid UUID: {ref.uuid}")

        # Desired state: duplicate host AND name detection
        if self.desired_nodes is not None:
            desired_node_hosts: list[str] = []
            desired_node_names: list[str] = []
            for i, dn in enumerate(self.desired_nodes):
                if dn.host:
                    if dn.host in desired_node_hosts:
                        errors.append(f"desired_nodes[{i}].host is a duplicate: {dn.host}")
                    desired_node_hosts.append(dn.host)
                if dn.name:
                    if dn.name in desired_node_names:
                        errors.append(f"desired_nodes[{i}].name is a duplicate: {dn.name}")
                    desired_node_names.append(dn.name)
                _append_hostname_error(errors, f"desired_nodes[{i}].sni", dn.sni)
                _append_hostname_error(errors, f"desired_nodes[{i}].domain", dn.domain)

        if self.desired_relays is not None:
            desired_relay_hosts: list[str] = []
            desired_relay_labels: set[str] = set()
            desired_node_host_set = {node.host for node in self.desired_nodes or [] if node.host}
            for i, dr in enumerate(self.desired_relays):
                if dr.host:
                    if dr.host in desired_relay_hosts:
                        errors.append(f"desired_relays[{i}].host is a duplicate: {dr.host}")
                    desired_relay_hosts.append(dr.host)
                    if dr.host in desired_node_host_set:
                        errors.append(
                            f"desired_relays[{i}].host also appears in desired_nodes: {dr.host}. "
                            "Use capability routing for same-server relay+exit designs."
                        )
                relay_label = _relay_label_value(dr.name, dr.host)
                _append_hostname_error(errors, f"desired_relays[{i}].sni", dr.sni)
                if relay_label:
                    if relay_label in desired_relay_labels:
                        errors.append(f"desired_relays[{i}].name creates a duplicate relay identity: {relay_label}")
                    desired_relay_labels.add(relay_label)

        workload_keys: set[tuple[str, int]] = set()
        active_workloads: set[str] = set()
        for i, workload in enumerate(self.workloads):
            label = f"workloads[{i}]"
            workload_key = (workload.id, workload.generation)
            if not workload.id:
                errors.append(f"{label}.id is empty")
            elif workload_key in workload_keys:
                errors.append(f"{label} duplicates workload generation {workload.id}@{workload.generation}")
            workload_keys.add(workload_key)
            if workload.active:
                if workload.id in active_workloads:
                    errors.append(f"{label}: workload {workload.id} has multiple active generations")
                active_workloads.add(workload.id)
            if workload.generation < 0:
                errors.append(f"{label}.generation cannot be negative")
            if workload.config_profile_uuid and not _is_valid_uuid(workload.config_profile_uuid):
                errors.append(f"{label}.config_profile_uuid is not a valid UUID: {workload.config_profile_uuid}")
            for server_ref, node_uuid in workload.node_uuids.items():
                if node_uuid and not _is_valid_uuid(node_uuid):
                    errors.append(f"{label}.node_uuids[{server_ref}] is not a valid UUID: {node_uuid}")
            for protocol, inbound_uuid in workload.inbound_uuids.items():
                if inbound_uuid and not _is_valid_uuid(inbound_uuid):
                    errors.append(f"{label}.inbound_uuids[{protocol}] is not a valid UUID: {inbound_uuid}")
            for server_ref, keys in workload.reality_keys.items():
                present = [bool(keys.public_key), bool(keys.private_key), bool(keys.short_id)]
                if any(present) and not all(present):
                    errors.append(
                        f"{label}.reality_keys[{server_ref}] must contain public, private, and short-ID values"
                    )
            if workload.desired_hash and re.fullmatch(r"[0-9a-f]{64}", workload.desired_hash) is None:
                errors.append(f"{label}.desired_hash is not a SHA-256 hash")

        for key, binding in self.managed_bindings.items():
            if key != f"{binding.logical_id}@{binding.generation}":
                errors.append(f"managed_bindings[{key}] key does not match its logical ID and generation")
            if binding.desired_hash and re.fullmatch(r"[0-9a-f]{64}", binding.desired_hash) is None:
                errors.append(f"managed_bindings[{key}].desired_hash is not a SHA-256 hash")
            if binding.observed_hash and re.fullmatch(r"[0-9a-f]{64}", binding.observed_hash) is None:
                errors.append(f"managed_bindings[{key}].observed_hash is not a SHA-256 hash")

        for key, allocation in self.allocations.items():
            if key != allocation.logical_id:
                errors.append(f"allocations[{key}] key does not match logical_id")
            if allocation.port and not _is_valid_port(allocation.port):
                errors.append(f"allocations[{key}].port is out of range: {allocation.port}")
            _append_path_error(errors, f"allocations[{key}].path", allocation.path)

        for key, checkpoint in self.action_checkpoints.items():
            if key != checkpoint.idempotency_key:
                errors.append(f"action_checkpoints[{key}] key does not match idempotency_key")
            if checkpoint.expected_hash and re.fullmatch(r"[0-9a-f]{64}", checkpoint.expected_hash) is None:
                errors.append(f"action_checkpoints[{key}].expected_hash is not a SHA-256 hash")
            if checkpoint.observed_hash and re.fullmatch(r"[0-9a-f]{64}", checkpoint.observed_hash) is None:
                errors.append(f"action_checkpoints[{key}].observed_hash is not a SHA-256 hash")
            if checkpoint.attempts < 0:
                errors.append(f"action_checkpoints[{key}].attempts cannot be negative")

        for field_name, value in (
            ("active_plan_hash", self.active_plan_hash),
            ("pending_plan_hash", self.pending_plan_hash),
        ):
            if value and re.fullmatch(r"[0-9a-f]{64}", value) is None:
                errors.append(f"{field_name} is not a SHA-256 hash")
        if self.active_generation < 0 or self.pending_generation < 0:
            errors.append("cluster generations cannot be negative")

        return errors

    def backup(self, path: Path | None = None) -> None:
        """Copy cluster.yml to cluster.yml.bak before mutations."""
        from meridian.cluster_persistence import backup_cluster

        backup_cluster(self, path)

    @property
    def is_configured(self) -> bool:
        """Whether the cluster has a panel configured."""
        return bool(self.panel.url and self.panel.api_token)

    @property
    def panel_node(self) -> NodeEntry | None:
        """Return the node that hosts the panel, if any."""
        for node in self.nodes:
            if node.is_panel_host:
                return node
        return None

    def find_node(self, query: str) -> NodeEntry | None:
        """Find a node by IP or name."""
        for node in self.nodes:
            if node.ip == query or node.name == query:
                return node
        return None

    def remove_node(self, ip_or_name: str) -> bool:
        """Remove a node by IP or name. Returns True if found and removed."""
        for i, node in enumerate(self.nodes):
            if node.ip == ip_or_name or node.name == ip_or_name:
                self.nodes.pop(i)
                return True
        return False

    def find_relay(self, query: str) -> RelayEntry | None:
        """Find a relay by IP or name."""
        for relay in self.relays:
            if relay.ip == query or relay.name == query:
                return relay
        return None

    def get_inbound(self, key: str | ProtocolKey) -> InboundRef | None:
        """Get a cached inbound reference by protocol key."""
        k = str(key)
        ref = self.inbounds.get(k)
        if ref is None:
            return None
        if isinstance(ref, dict):
            from meridian.cluster_persistence import _INBOUND_REF_FIELDS, _load_dataclass

            self.inbounds[k] = _load_dataclass(ref, InboundRef, _INBOUND_REF_FIELDS)
            return self.inbounds[k]
        return ref


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _is_valid_ip(s: str) -> bool:
    """Check if a string is a valid IPv4 or IPv6 address."""
    import ipaddress

    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def _is_valid_uuid(s: str) -> bool:
    """Check if a string matches UUID format (lowercase or uppercase hex)."""
    import re

    return bool(
        re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            s,
            re.IGNORECASE,
        )
    )


def _is_valid_port(port: int) -> bool:
    """Check if a port number is in the valid range."""
    return isinstance(port, int) and 1 <= port <= 65535


def _relay_label_value(name: str, ip: str) -> str:
    """Return the relay identity used by nginx/Remnawave remark names."""
    value = name or ip
    return re.sub(r"[^a-zA-Z0-9_-]", "-", value) if value else ""


def _append_hostname_error(errors: list[str], field_name: str, value: str) -> None:
    if not value:
        return
    try:
        normalized = validate_hostname_value(value)
    except ValueError as exc:
        errors.append(f"{field_name} is invalid: {exc}")
        return
    if normalized != value:
        errors.append(f"{field_name} must use canonical form: {normalized}")


def _append_path_error(errors: list[str], field_name: str, value: str) -> None:
    if not value:
        return
    try:
        normalized = validate_optional_transport_path_value(value)
    except ValueError as exc:
        errors.append(f"{field_name} is invalid: {exc}")
        return
    if normalized != value:
        errors.append(f"{field_name} must be relative without a leading slash")
