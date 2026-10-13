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
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import quote, urlsplit

from meridian.core.inputs import is_ip, validate_optional_transport_path_value
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

    def subscription_url(self, short_uuid: str) -> str:
        """Public subscription URL for a user; empty when it cannot be built.

        Lives under the connection-page path (``sub_path``) so links shared
        with end users never reveal the admin panel's secret path.
        """
        if not self.url or not self.sub_path or not short_uuid:
            return ""
        parsed = urlsplit(self.url)
        return f"{parsed.scheme}://{parsed.netloc}/{self.sub_path}/sub/{quote(short_uuid, safe='')}"

    @property
    def display_url(self) -> str:
        """Panel URL with trailing slash for browser compatibility."""
        if not self.url:
            return ""
        return self.url.rstrip("/") + "/"


@dataclass
class BrandingConfig:
    """Server branding for connection pages."""

    server_name: str = ""  # display name (e.g., "Alice's VPN")
    icon: str = ""  # emoji or data URI
    color: str = ""  # palette name (ocean, sunset, forest, lavender, rose, slate)
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
    server_ref: str = ""  # host owning server-side artifacts, needed to retire them
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


ActionCheckpointStatus = Literal["pending", "running", "unknown", "succeeded", "failed", "skipped", "cancelled"]


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

    Stored as cluster.yml in Meridian's home directory. One file replaces per-server proxy.yml.
    Client/user state lives in Remnawave's database, not here.
    """

    version: int = CURRENT_CLUSTER_VERSION
    panel: PanelConfig = field(default_factory=PanelConfig)
    branding: BrandingConfig = field(default_factory=BrandingConfig)
    # Subscription page container path and deployment evidence (set by provisioning).
    subscription_page: SubscriptionPageConfig | None = None
    # Telegram notifications (None = not configured, disable in panel)
    telegram: TelegramConfig | None = None
    # Workload-scoped topology and durable compiled-resource state.
    topology_intent: SetupIntent | None = None
    workloads: list[WorkloadBinding] = field(default_factory=list)
    managed_bindings: dict[str, ManagedResourceBinding] = field(default_factory=dict)
    allocations: dict[str, ResourceAllocation] = field(default_factory=dict)
    action_checkpoints: dict[str, ActionCheckpoint] = field(default_factory=dict)
    connection_page_markers: list[str] = field(default_factory=list)
    # Newer Remnawave releases `fleet upgrade` moved past this CLI's pins
    # (runtime pin name -> tag@sha256 image); see remnawave.versions.effective_pins.
    image_pins: dict[str, str] = field(default_factory=dict)
    # Random ID shared by every copy of this fleet's state; the control server's
    # apply fence refuses copies with another ID (see state_fence.py).
    fleet_id: str = ""
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

    def validate(self) -> list[str]:
        """Validate cluster config consistency. Returns list of error strings (empty = valid)."""
        errors: list[str] = []
        if self.is_configured:
            if not self.panel.url:
                errors.append("panel.url is empty but cluster is marked as configured")
            if not self.panel.api_token:
                errors.append("panel.api_token is empty but cluster is marked as configured")

        # Panel server_ip
        if self.panel.server_ip and not is_ip(self.panel.server_ip):
            errors.append(f"panel.server_ip is not a valid IP: {self.panel.server_ip}")

        # Panel ssh_port
        if not _is_valid_port(self.panel.ssh_port):
            errors.append(f"panel.ssh_port is out of range: {self.panel.ssh_port}")

        # Panel URL format
        if self.panel.url:
            from urllib.parse import urlparse

            parsed = urlparse(self.panel.url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                errors.append(f"panel.url is not a valid HTTP(S) URL: {self.panel.url}")

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

        markers = self.connection_page_markers
        if len(markers) != len(set(markers)):
            errors.append("connection_page_markers contains duplicate markers")
        if any(re.fullmatch(r"[0-9a-f]{64}", marker) is None for marker in markers):
            errors.append("connection_page_markers contains an invalid SHA-256 marker")

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
