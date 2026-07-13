"""YAML persistence for ClusterConfig.

Handles serialization, deserialization, field derivation, and atomic file I/O.
The data model lives in ``cluster.py``; this module reads/writes it.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import MISSING, asdict, fields
from pathlib import Path
from typing import Any

import yaml

from meridian.cluster import (
    CURRENT_CLUSTER_VERSION,
    AppliedState,
    BrandingConfig,
    ClusterConfig,
    ClusterConfigExternallyModifiedError,
    DesiredNode,
    DesiredRelay,
    InboundRef,
    NodeEntry,
    PanelConfig,
    RelayEntry,
    SubscriptionPageConfig,
    TelegramConfig,
    _load_warning,
)

logger = logging.getLogger("meridian.cluster")


# ---------------------------------------------------------------------------
# Field-set derivation (must live after dataclass imports)
# ---------------------------------------------------------------------------


def _public_fields(cls: type) -> set[str]:
    """Return the set of non-underscored field names from a dataclass."""
    return {f.name for f in fields(cls) if not f.name.startswith("_")}


_PANEL_FIELDS = _public_fields(PanelConfig)
_NODE_FIELDS = _public_fields(NodeEntry)
_RELAY_FIELDS = _public_fields(RelayEntry)
_BRANDING_FIELDS = _public_fields(BrandingConfig)
_INBOUND_REF_FIELDS = _public_fields(InboundRef)
_SUBSCRIPTION_PAGE_FIELDS = _public_fields(SubscriptionPageConfig)
_DESIRED_NODE_FIELDS = _public_fields(DesiredNode)
_DESIRED_RELAY_FIELDS = _public_fields(DesiredRelay)
_PRE_V4_1_APPLIED_KEYS = {
    "desired_clients_applied",
    "desired_nodes_applied",
    "desired_relays_applied",
}
_KNOWN_TOP = _public_fields(ClusterConfig) | _PRE_V4_1_APPLIED_KEYS


_SSH_DEFAULTS: dict[str, Any] = {"ssh_user": "root", "ssh_port": 22}
"""Default SSH credentials — shared by panel, node, relay, and desired-state entries."""


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _strip_ssh_defaults(d: dict[str, Any]) -> None:
    """Remove ssh_user/ssh_port from *d* when they match the defaults.

    Keeps YAML output clean — readers re-apply the same defaults on load.
    """
    if d.get("ssh_user") == "root":
        d.pop("ssh_user", None)
    if d.get("ssh_port") == 22:
        d.pop("ssh_port", None)


def _strip_none(d: dict[str, Any]) -> dict[str, Any]:
    """Remove keys with None values."""
    return {k: v for k, v in d.items() if v is not None}


def _stringify_keys(d: dict[Any, Any]) -> dict[str, Any]:
    """Convert all dict keys to plain strings (handles StrEnum keys)."""
    return {str(k): v for k, v in d.items()}


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
    _strip_ssh_defaults(panel_dict)
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
            _strip_ssh_defaults(d)
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
            if d.get("hysteria2", True):
                d.pop("hysteria2", None)
            nodes_out.append(d)
        out["nodes"] = nodes_out

    # Relays
    if cfg.relays:
        relays_out = []
        for relay in cfg.relays:
            d = _serialize_dataclass(relay)
            _strip_ssh_defaults(d)
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
        # Only include deployed if True (keep YAML clean)
        if not sub_page_dict.get("deployed"):
            sub_page_dict.pop("deployed", None)
        if sub_page_dict:
            out["subscription_page"] = sub_page_dict

    # Telegram notifications (v2 — only serialize if configured)
    if cfg.telegram is not None:
        tg_dict = _serialize_dataclass(cfg.telegram)
        if tg_dict:
            out["telegram"] = tg_dict

    # Desired state (v2 — serialize if present in config, even if empty)
    # An explicit empty list means "manage this type, want zero" vs absent = "don't manage".
    if cfg.desired_nodes is not None:
        desired_nodes_out = []
        for dn in cfg.desired_nodes:
            d = _serialize_dataclass(dn)
            _strip_ssh_defaults(d)
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
            _strip_ssh_defaults(d)
            desired_relays_out.append(d)
        out["desired_relays"] = desired_relays_out

    # Applied state (v2 — reconciler snapshot, only serialize non-None fields)
    applied_dict: dict[str, Any] = {}
    if cfg.applied_state.nodes is not None:
        applied_dict["nodes"] = list(cfg.applied_state.nodes)
    if cfg.applied_state.clients is not None:
        applied_dict["clients"] = list(cfg.applied_state.clients)
    if cfg.applied_state.relays is not None:
        applied_dict["relays"] = list(cfg.applied_state.relays)
    if applied_dict:
        out["applied_state"] = applied_dict

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
    if not isinstance(raw, dict):
        raw = {}
    defaults = defaults or {}
    transforms = transforms or {}

    # Build a map of field name → default value for None coercion
    field_defaults: dict[str, Any] = {}
    for f in fields(cls):
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


def _load_applied_list(raw: Any) -> list[str] | None:
    """Parse an applied-state list from YAML, coercing to list[str] | None.

    Returns None for absent/non-list values (no history).
    Empty list is preserved (managed, converged to zero).
    Filters non-string items defensively.
    """
    if not isinstance(raw, list):
        return None
    # Preserve empty list — it means "managed, converged to zero" (distinct
    # from None which means "no history"). Filtering non-strings is defensive.
    return [item for item in raw if isinstance(item, str)]


def _load_applied_state(raw: dict[str, Any]) -> AppliedState:
    """Load AppliedState from a parsed YAML mapping."""
    return AppliedState(
        nodes=_load_applied_list(raw.get("nodes")),
        clients=_load_applied_list(raw.get("clients")),
        relays=_load_applied_list(raw.get("relays")),
    )


def _migrate_pre_v4_1_applied_state(data: dict[str, Any]) -> AppliedState:
    """Load v4.0 top-level applied snapshots into the typed v4.1 shape."""
    return AppliedState(
        nodes=_load_applied_list(data.get("desired_nodes_applied")),
        clients=_load_applied_list(data.get("desired_clients_applied")),
        relays=_load_applied_list(data.get("desired_relays_applied")),
    )


def _load_cluster(data: dict[str, Any]) -> ClusterConfig:
    """Load a cluster config from parsed YAML."""
    # Panel
    panel = _load_dataclass(
        data.get("panel", {}),
        PanelConfig,
        _PANEL_FIELDS,
        defaults={**_SSH_DEFAULTS},
    )

    # Nodes
    nodes: list[NodeEntry] = []
    for n in data.get("nodes", []):
        nodes.append(
            _load_dataclass(
                n,
                NodeEntry,
                _NODE_FIELDS,
                defaults={**_SSH_DEFAULTS, "is_panel_host": False, "warp": False, "hysteria2": True},
                transforms={"is_panel_host": bool, "warp": bool, "hysteria2": bool},
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
                defaults={**_SSH_DEFAULTS, "port": 443},
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

    # Telegram notifications (v2) — None = unconfigured (disabled in panel)
    telegram: TelegramConfig | None = None
    if data.get("telegram") is not None:
        telegram = _load_dataclass(
            data.get("telegram"),
            TelegramConfig,
            _public_fields(TelegramConfig),
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
                    defaults={**_SSH_DEFAULTS, "warp": None},
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
                    defaults={**_SSH_DEFAULTS},
                )
            )

    # Applied state (v2): v4.0 stored snapshots as top-level keys.
    _applied_raw = data.get("applied_state")
    applied_state = (
        _load_applied_state(_applied_raw) if isinstance(_applied_raw, dict) else _migrate_pre_v4_1_applied_state(data)
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
        telegram=telegram,
        desired_nodes=desired_nodes,
        desired_clients=desired_clients,
        desired_relays=desired_relays,
        applied_state=applied_state,
        _extra=extra,
    )


# ---------------------------------------------------------------------------
# Public free functions
# ---------------------------------------------------------------------------


def load_cluster(path: Path | None = None) -> ClusterConfig:
    """Load ClusterConfig from cluster.yml. Returns empty config if file doesn't exist."""
    if path is None:
        from meridian.config import CLUSTER_CONFIG

        path = CLUSTER_CONFIG
    logger.debug("Loading cluster config from %s", path)
    if not path.exists():
        return ClusterConfig()
    load_mtime_ns = path.stat().st_mtime_ns
    raw = path.read_text()
    if not raw.strip():
        return ClusterConfig()
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        _load_warning(f"cluster.yml is corrupted and could not be parsed: {e}")
        return ClusterConfig()
    if not isinstance(data, dict):
        return ClusterConfig()
    version = data.get("version", CURRENT_CLUSTER_VERSION)
    if isinstance(version, int) and version > CURRENT_CLUSTER_VERSION:
        _load_warning(
            f"cluster.yml has version {version}, but this CLI only understands version {CURRENT_CLUSTER_VERSION}. "
            "Some fields may be ignored. Upgrade Meridian: pip install --upgrade meridian-vpn"
        )
    data.setdefault("version", CURRENT_CLUSTER_VERSION)
    cfg = _load_cluster(data)
    cfg._loaded_mtime_ns = load_mtime_ns

    # Mark future-version configs as read-only to prevent data loss
    if isinstance(version, int) and version > CURRENT_CLUSTER_VERSION:
        cfg._readonly = True

    # Warn about validation errors on load (don't hard-fail — recover/doctor need corrupt configs)
    errors = cfg.validate()
    if errors:
        details = [f"- {err}" for err in errors[:3]]
        if len(errors) > 3:
            details.append(f"... and {len(errors) - 3} more")
        _load_warning(f"cluster.yml has {len(errors)} validation issue(s):", details=details)

    return cfg


def save_cluster(cfg: ClusterConfig, path: Path | None = None) -> None:
    """Write ClusterConfig to cluster.yml (atomic via tempfile+rename).

    Thread-safe: acquires the config's internal lock to prevent concurrent
    saves from parallel node provisioning.
    """
    with cfg._lock:
        _save_locked(cfg, path)


def _save_locked(cfg: ClusterConfig, path: Path | None = None) -> None:
    """Internal save implementation (called under lock)."""
    if cfg._readonly:
        raise ValueError(
            "Cannot save: cluster.yml has a newer version than this CLI supports. "
            "Upgrade Meridian to avoid data loss: pip install --upgrade meridian-vpn"
        )
    if path is None:
        from meridian.config import CLUSTER_CONFIG

        path = CLUSTER_CONFIG
    logger.debug("Saving cluster config to %s", path)

    errors = cfg.validate()
    if errors:
        raise ValueError(
            f"Cluster config has {len(errors)} validation error(s):\n" + "\n".join(f"  - {e}" for e in errors[:5])
        )

    # Guard against clobbering external edits made during a long apply.
    if cfg._loaded_mtime_ns is not None and path.exists():
        current_mtime_ns = path.stat().st_mtime_ns
        if current_mtime_ns > cfg._loaded_mtime_ns:
            raise ClusterConfigExternallyModifiedError(
                f"Refusing to save {path}: file was modified externally since we "
                f"loaded it (loaded={cfg._loaded_mtime_ns}, now={current_mtime_ns}). "
                "Reload cluster.yml and retry."
            )

    out = _serialize_cluster(cfg)

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
            cfg._loaded_mtime_ns = path.stat().st_mtime_ns
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


def backup_cluster(cfg: ClusterConfig, path: Path | None = None) -> None:
    """Copy cluster.yml to cluster.yml.bak before mutations."""
    if path is None:
        from meridian.config import CLUSTER_CONFIG

        path = CLUSTER_CONFIG
    if path.exists():
        from meridian.config import CLUSTER_BACKUP

        shutil.copy2(str(path), str(CLUSTER_BACKUP))
