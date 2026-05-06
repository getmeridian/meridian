"""Fleet configuration with YAML persistence.

Replaces the per-server credentials.py with a single cluster-wide manifest.
Users/clients live in Remnawave's PostgreSQL — this file stores only
deployment topology and panel access.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("meridian.cluster")


def _load_warning(message: str, *, details: list[str] | None = None) -> None:
    """Record cluster load warnings without importing CLI renderers."""
    if details:
        logger.warning("%s %s", message, " ".join(details))
    else:
        logger.warning("%s", message)


class ClusterConfigExternallyModifiedError(RuntimeError):
    """cluster.yml was modified on disk between load() and save().

    Raised so callers don't silently clobber the user's (or another
    process's) writes. Most callers should surface the error and ask
    the user to retry after reloading.
    """


# StrEnum backport for Python 3.10 (stdlib StrEnum is 3.11+)
class StrEnum(str, Enum):
    """String enum compatible with Python 3.10+."""

    def __str__(self) -> str:
        return self.value


class ProtocolKey(StrEnum):
    """Protocol identifiers — single source of truth for string keys."""

    REALITY = "reality"
    XHTTP = "xhttp"
    WSS = "wss"


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
class ClusterConfig:
    """Fleet-wide configuration — the sole local state for Meridian 4.0.

    Stored at ~/.meridian/cluster.yml. One file replaces per-server proxy.yml.
    Client/user state lives in Remnawave's database, not here.
    """

    version: int = 2
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
    # v2: desired state for declarative plan/apply workflow
    # None = not declared (don't manage). [] = declared empty (manage, want zero).
    desired_nodes: list[DesiredNode] | None = None
    desired_clients: list[str] | None = None
    desired_relays: list[DesiredRelay] | None = None
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
        if path is None:
            from meridian.config import CLUSTER_CONFIG

            path = CLUSTER_CONFIG
        logger.debug("Loading cluster config from %s", path)
        if not path.exists():
            return cls()
        load_mtime_ns = path.stat().st_mtime_ns
        raw = path.read_text()
        if not raw.strip():
            return cls()
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as e:
            _load_warning(f"cluster.yml is corrupted and could not be parsed: {e}")
            return cls()
        if not isinstance(data, dict):
            return cls()
        version = data.get("version", 1)
        if isinstance(version, int) and version > 2:
            _load_warning(
                f"cluster.yml has version {version}, but this CLI only understands version 2. "
                "Some fields may be ignored. Upgrade Meridian: pip install --upgrade meridian-vpn"
            )
        from meridian.migrations import migrate

        data = migrate(data)
        cfg = _load_cluster(data)
        cfg._loaded_mtime_ns = load_mtime_ns

        # Mark future-version configs as read-only to prevent data loss
        if isinstance(version, int) and version > 2:
            cfg._readonly = True

        # Warn about validation errors on load (don't hard-fail — recover/doctor need corrupt configs)
        errors = cfg.validate()
        if errors:
            details = [f"- {err}" for err in errors[:3]]
            if len(errors) > 3:
                details.append(f"... and {len(errors) - 3} more")
            _load_warning(f"cluster.yml has {len(errors)} validation issue(s):", details=details)

        return cfg

    def save(self, path: Path | None = None) -> None:
        """Write to cluster.yml (atomic via tempfile+rename).

        Thread-safe: acquires an internal lock to prevent concurrent
        saves from parallel node provisioning.
        """
        with self._lock:
            self._save_locked(path)

    def _save_locked(self, path: Path | None = None) -> None:
        """Internal save implementation (called under lock)."""
        if self._readonly:
            raise ValueError(
                "Cannot save: cluster.yml has a newer version than this CLI supports. "
                "Upgrade Meridian to avoid data loss: pip install --upgrade meridian-vpn"
            )
        if path is None:
            from meridian.config import CLUSTER_CONFIG

            path = CLUSTER_CONFIG
        logger.debug("Saving cluster config to %s", path)

        errors = self.validate()
        if errors:
            raise ValueError(
                f"Cluster config has {len(errors)} validation error(s):\n" + "\n".join(f"  - {e}" for e in errors[:5])
            )

        # Guard against clobbering external edits made during a long apply.
        # If the file on disk has a newer mtime than when we loaded it,
        # someone (user, another CLI, config management) wrote to it. Bail
        # instead of silently overwriting their changes. Callers can retry
        # by reloading the config.
        if self._loaded_mtime_ns is not None and path.exists():
            current_mtime_ns = path.stat().st_mtime_ns
            if current_mtime_ns > self._loaded_mtime_ns:
                raise ClusterConfigExternallyModifiedError(
                    f"Refusing to save {path}: file was modified externally since we "
                    f"loaded it (loaded={self._loaded_mtime_ns}, now={current_mtime_ns}). "
                    "Reload cluster.yml and retry."
                )

        out = _serialize_cluster(self)

        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Enforce dir permissions only for directories we create (not system temp dirs)
        try:
            path.parent.chmod(0o700)
        except PermissionError:
            pass

        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            os.write(fd, yaml.dump(out, default_flow_style=False, sort_keys=False).encode())
            os.fsync(fd)
            os.close(fd)
            fd = -1
            os.chmod(tmp, 0o600)
            os.rename(tmp, str(path))
            # Track our own write so subsequent save() calls don't trip the
            # external-edit guard on their own previous save.
            try:
                self._loaded_mtime_ns = path.stat().st_mtime_ns
            except OSError:
                pass
        except OSError as e:
            if fd >= 0:
                os.close(fd)
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise OSError(
                f"Failed to save cluster.yml: {e}. Check disk space (df -h) and directory permissions ({path.parent})"
            ) from e
        except BaseException:
            if fd >= 0:
                os.close(fd)
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

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

        if self.desired_relays is not None:
            desired_relay_hosts: list[str] = []
            desired_relay_labels: set[str] = set()
            desired_node_hosts = {node.host for node in self.desired_nodes or [] if node.host}
            for i, dr in enumerate(self.desired_relays):
                if dr.host:
                    if dr.host in desired_relay_hosts:
                        errors.append(f"desired_relays[{i}].host is a duplicate: {dr.host}")
                    desired_relay_hosts.append(dr.host)
                    if dr.host in desired_node_hosts:
                        errors.append(
                            f"desired_relays[{i}].host also appears in desired_nodes: {dr.host}. "
                            "Use capability routing for same-server relay+exit designs."
                        )
                relay_label = _relay_label_value(dr.name, dr.host)
                if relay_label:
                    if relay_label in desired_relay_labels:
                        errors.append(f"desired_relays[{i}].name creates a duplicate relay identity: {relay_label}")
                    desired_relay_labels.add(relay_label)

        return errors

    def backup(self, path: Path | None = None) -> None:
        """Copy cluster.yml to cluster.yml.bak before mutations."""
        if path is None:
            from meridian.config import CLUSTER_CONFIG

            path = CLUSTER_CONFIG
        if path.exists():
            from meridian.config import CLUSTER_BACKUP

            shutil.copy2(str(path), str(CLUSTER_BACKUP))

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


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

_PANEL_FIELDS = {
    "url",
    "api_token",
    "admin_user",
    "admin_pass",
    "server_ip",
    "ssh_user",
    "ssh_port",
    "secret_path",
    "sub_path",
    "deployed_with",
}
_NODE_FIELDS = {
    "ip",
    "uuid",
    "name",
    "ssh_user",
    "ssh_port",
    "sni",
    "domain",
    "is_panel_host",
    "deployed_with",
    "xhttp_path",
    "ws_path",
    "reality_public_key",
    "reality_short_id",
    "reality_private_key",
    "warp",
}
_RELAY_FIELDS = {"ip", "name", "port", "exit_node_ip", "host_uuids", "sni", "ssh_user", "ssh_port"}
_BRANDING_FIELDS = {"server_name", "icon", "color"}
_INBOUND_REF_FIELDS = {"uuid", "tag"}
_SUBSCRIPTION_PAGE_FIELDS = {"enabled", "path"}
_DESIRED_NODE_FIELDS = {"host", "name", "ssh_user", "ssh_port", "domain", "sni", "warp"}
_DESIRED_RELAY_FIELDS = {"host", "name", "exit_node", "sni", "ssh_user", "ssh_port"}
_KNOWN_TOP = {
    "version",
    "panel",
    "config_profile_uuid",
    "config_profile_name",
    "squad_uuid",
    "nodes",
    "relays",
    "branding",
    "inbounds",
    "subscription_page",
    "desired_nodes",
    "desired_clients",
    "desired_relays",
}


def _strip_none(d: dict[str, Any]) -> dict[str, Any]:
    """Remove keys with None values."""
    return {k: v for k, v in d.items() if v is not None}


def _stringify_keys(d: dict[Any, Any]) -> dict[str, Any]:
    """Convert all dict keys to plain strings (handles StrEnum keys)."""
    return {str(k): v for k, v in d.items()}


def _relay_label_value(name: str, ip: str) -> str:
    """Return the relay identity used by nginx/Remnawave remark names."""
    value = name or ip
    return re.sub(r"[^a-zA-Z0-9_-]", "-", value) if value else ""


def _serialize_dataclass(obj: Any) -> dict[str, Any]:
    """Serialize a dataclass, merging _extra fields back in."""
    data = _strip_none({k: v for k, v in asdict(obj).items() if k != "_extra"})
    # Convert any dict values with enum keys to plain strings
    for key, val in data.items():
        if isinstance(val, dict):
            data[key] = _stringify_keys(val)
    extra = getattr(obj, "_extra", {})
    if isinstance(extra, dict):
        for k, v in extra.items():
            if k not in data:
                data[k] = v
    return data


def _serialize_cluster(cfg: ClusterConfig) -> dict[str, Any]:
    """Serialize ClusterConfig to a dict for YAML output."""
    out: dict[str, Any] = {"version": cfg.version}

    # Panel
    panel_dict = _serialize_dataclass(cfg.panel)
    # Remove default values to keep YAML clean
    if panel_dict.get("ssh_user") == "root":
        panel_dict.pop("ssh_user", None)
    if panel_dict.get("ssh_port") == 22:
        panel_dict.pop("ssh_port", None)
    if panel_dict:
        out["panel"] = panel_dict

    # Config profile
    if cfg.config_profile_uuid:
        out["config_profile_uuid"] = cfg.config_profile_uuid
    if cfg.config_profile_name:
        out["config_profile_name"] = cfg.config_profile_name
    if cfg.squad_uuid:
        out["squad_uuid"] = cfg.squad_uuid

    # Nodes
    if cfg.nodes:
        nodes_out = []
        for node in cfg.nodes:
            d = _serialize_dataclass(node)
            if d.get("ssh_user") == "root":
                d.pop("ssh_user", None)
            if d.get("ssh_port") == 22:
                d.pop("ssh_port", None)
            if not d.get("is_panel_host"):
                d.pop("is_panel_host", None)
            if not d.get("xhttp_path"):
                d.pop("xhttp_path", None)
            if not d.get("ws_path"):
                d.pop("ws_path", None)
            if not d.get("reality_public_key"):
                d.pop("reality_public_key", None)
            if not d.get("reality_short_id"):
                d.pop("reality_short_id", None)
            if not d.get("reality_private_key"):
                d.pop("reality_private_key", None)
            if not d.get("warp"):
                d.pop("warp", None)
            nodes_out.append(d)
        out["nodes"] = nodes_out

    # Relays
    if cfg.relays:
        relays_out = []
        for relay in cfg.relays:
            d = _serialize_dataclass(relay)
            if d.get("ssh_user") == "root":
                d.pop("ssh_user", None)
            if d.get("ssh_port") == 22:
                d.pop("ssh_port", None)
            relays_out.append(d)
        out["relays"] = relays_out

    # Branding
    branding_dict = _serialize_dataclass(cfg.branding)
    for f in ("server_name", "icon", "color"):
        if branding_dict.get(f) == "":
            branding_dict.pop(f, None)
    if branding_dict:
        out["branding"] = branding_dict

    # Inbounds
    if cfg.inbounds:
        inbounds_out: dict[str, Any] = {}
        for key, ref in cfg.inbounds.items():
            str_key = str(key)  # ProtocolKey → plain string
            if hasattr(ref, "__dataclass_fields__"):
                inbounds_out[str_key] = _serialize_dataclass(ref)
            elif isinstance(ref, dict):
                inbounds_out[str_key] = _strip_none(ref)
            else:
                inbounds_out[str_key] = ref
        if inbounds_out:
            out["inbounds"] = inbounds_out

    # Subscription page (v2 — only serialize if explicitly declared)
    if cfg.subscription_page is not None:
        sub_page_dict = _serialize_dataclass(cfg.subscription_page)
        if sub_page_dict:
            out["subscription_page"] = sub_page_dict

    # Desired state (v2 — serialize if present in config, even if empty)
    # An explicit empty list means "manage this type, want zero" vs absent = "don't manage".
    if cfg.desired_nodes is not None:
        desired_nodes_out = []
        for dn in cfg.desired_nodes:
            d = _serialize_dataclass(dn)
            if d.get("ssh_user") == "root":
                d.pop("ssh_user", None)
            if d.get("ssh_port") == 22:
                d.pop("ssh_port", None)
            # Preserve warp: None → YAML null, False, True are all meaningful.
            # _serialize_dataclass strips None via _strip_none, so re-add it.
            d["warp"] = dn.warp
            desired_nodes_out.append(d)
        out["desired_nodes"] = desired_nodes_out

    if cfg.desired_clients is not None:
        out["desired_clients"] = list(cfg.desired_clients)

    if cfg.desired_relays is not None:
        desired_relays_out = []
        for dr in cfg.desired_relays:
            d = _serialize_dataclass(dr)
            if d.get("ssh_user") == "root":
                d.pop("ssh_user", None)
            if d.get("ssh_port") == 22:
                d.pop("ssh_port", None)
            desired_relays_out.append(d)
        out["desired_relays"] = desired_relays_out

    # Extra fields (forward-compat)
    for k, v in cfg._extra.items():
        if k not in out:
            out[k] = v

    return out


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------


def _load_dataclass(
    raw: Any,
    cls: type[Any],
    known_fields: set[str],
    *,
    defaults: dict[str, Any] | None = None,
    transforms: dict[str, Any] | None = None,
) -> Any:
    """Load known fields into a dataclass, preserving unknown ones in _extra.

    YAML ``null`` values are coerced to the field's dataclass default so that
    downstream code never sees ``None`` on a ``str``/``int``/``bool`` field.
    """
    from dataclasses import MISSING
    from dataclasses import fields as dataclass_fields

    if not isinstance(raw, dict):
        raw = {}
    defaults = defaults or {}
    transforms = transforms or {}

    # Build a map of field name → default value for None coercion
    field_defaults: dict[str, Any] = {}
    for f in dataclass_fields(cls):
        if f.name.startswith("_"):
            continue
        if f.default is not MISSING:
            field_defaults[f.name] = f.default

    values: dict[str, Any] = {}
    for field_name in known_fields:
        if field_name in raw:
            value = raw[field_name]
        elif field_name in defaults:
            value = defaults[field_name]
        else:
            continue
        # Coerce YAML null → dataclass default (e.g., None → "" for str fields)
        if value is None and field_name in field_defaults:
            value = field_defaults[field_name]
        if field_name in transforms:
            value = transforms[field_name](value)
        values[field_name] = value
    extra = {k: v for k, v in raw.items() if k not in known_fields}
    return cls(**values, _extra=extra)


def _load_cluster(data: dict[str, Any]) -> ClusterConfig:
    """Load a cluster config from parsed YAML."""
    # Panel
    panel = _load_dataclass(
        data.get("panel", {}),
        PanelConfig,
        _PANEL_FIELDS,
        defaults={"ssh_user": "root", "ssh_port": 22},
    )

    # Nodes
    nodes: list[NodeEntry] = []
    for n in data.get("nodes", []):
        nodes.append(
            _load_dataclass(
                n,
                NodeEntry,
                _NODE_FIELDS,
                defaults={"ssh_user": "root", "ssh_port": 22, "is_panel_host": False, "warp": False},
                transforms={"is_panel_host": bool, "warp": bool},
            )
        )

    # Relays
    relays: list[RelayEntry] = []
    for r in data.get("relays", []):
        relays.append(
            _load_dataclass(
                r,
                RelayEntry,
                _RELAY_FIELDS,
                defaults={"ssh_user": "root", "ssh_port": 22, "port": 443},
            )
        )

    # Branding
    branding = _load_dataclass(
        data.get("branding", {}),
        BrandingConfig,
        _BRANDING_FIELDS,
        defaults={"server_name": "", "icon": "", "color": ""},
    )

    # Inbounds
    inbounds: dict[str, InboundRef] = {}
    for key, ref_data in data.get("inbounds", {}).items():
        inbounds[key] = _load_dataclass(ref_data, InboundRef, _INBOUND_REF_FIELDS)

    # Subscription page (v2) — tri-state:
    #   key absent OR explicit `null` → None (unmanaged, do nothing)
    #   mapping (even `{}`) → load as managed config
    subscription_page: SubscriptionPageConfig | None = None
    if data.get("subscription_page") is not None:
        subscription_page = _load_dataclass(
            data.get("subscription_page"),
            SubscriptionPageConfig,
            _SUBSCRIPTION_PAGE_FIELDS,
            defaults={"enabled": True},
            transforms={"enabled": bool},
        )

    # Desired state (v2) — tri-state semantics per cluster.example.yml:
    #   key absent OR explicit `null` → None (unmanaged)
    #   `[]` → managed, want zero
    #   `[...]` → managed, want these
    desired_nodes: list[DesiredNode] | None = None
    _desired_nodes_raw = data.get("desired_nodes")
    if _desired_nodes_raw is not None:
        if not isinstance(_desired_nodes_raw, list):
            raise ValueError(f"desired_nodes must be a list or null, got {type(_desired_nodes_raw).__name__}")
        desired_nodes = []
        for dn in _desired_nodes_raw:
            desired_nodes.append(
                _load_dataclass(
                    dn,
                    DesiredNode,
                    _DESIRED_NODE_FIELDS,
                    defaults={"ssh_user": "root", "ssh_port": 22, "warp": None},
                )
            )

    desired_clients: list[str] | None = None
    _desired_clients_raw = data.get("desired_clients")
    if _desired_clients_raw is not None:
        if not isinstance(_desired_clients_raw, list):
            raise ValueError(f"desired_clients must be a list or null, got {type(_desired_clients_raw).__name__}")
        desired_clients = [str(c) for c in _desired_clients_raw]

    desired_relays: list[DesiredRelay] | None = None
    _desired_relays_raw = data.get("desired_relays")
    if _desired_relays_raw is not None:
        if not isinstance(_desired_relays_raw, list):
            raise ValueError(f"desired_relays must be a list or null, got {type(_desired_relays_raw).__name__}")
        desired_relays = []
        for dr in _desired_relays_raw:
            desired_relays.append(
                _load_dataclass(
                    dr,
                    DesiredRelay,
                    _DESIRED_RELAY_FIELDS,
                    defaults={"ssh_user": "root", "ssh_port": 22},
                )
            )

    # Extra fields
    extra = {k: v for k, v in data.items() if k not in _KNOWN_TOP}

    return ClusterConfig(
        version=data.get("version", 2),
        panel=panel,
        config_profile_uuid=data.get("config_profile_uuid", ""),
        config_profile_name=data.get("config_profile_name", ""),
        squad_uuid=data.get("squad_uuid", ""),
        nodes=nodes,
        relays=relays,
        branding=branding,
        inbounds=inbounds,
        subscription_page=subscription_page,
        desired_nodes=desired_nodes,
        desired_clients=desired_clients,
        desired_relays=desired_relays,
        _extra=extra,
    )
