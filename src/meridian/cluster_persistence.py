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

from meridian.binary_pins import BinaryPin
from meridian.cluster import (
    CURRENT_CLUSTER_VERSION,
    BrandingConfig,
    ClusterConfig,
    ClusterConfigExternallyModifiedError,
    PanelConfig,
    SubscriptionPageConfig,
    TelegramConfig,
    _load_warning,
)
from meridian.cluster_v3 import load_cluster_v3, serialize_cluster_v3, validate_cluster_v3_structure
from meridian.core.errors import LocalStateCorruptedError, LocalStateError

logger = logging.getLogger("meridian.cluster")


# Field sets derive from the dataclasses, so they must follow those imports.


def _public_fields(cls: type) -> set[str]:
    """Return the set of non-underscored field names from a dataclass."""
    return {f.name for f in fields(cls) if not f.name.startswith("_")}


_PANEL_FIELDS = _public_fields(PanelConfig)
_BRANDING_FIELDS = _public_fields(BrandingConfig)
_SUBSCRIPTION_PAGE_FIELDS = _public_fields(SubscriptionPageConfig)
_KNOWN_TOP = _public_fields(ClusterConfig)


def _load_binary_pins(raw: object) -> dict[str, BinaryPin]:
    """Read saved binary pins by shape only; `effective_binary_pins` decides which to trust."""
    if not isinstance(raw, dict):
        return {}
    pins: dict[str, BinaryPin] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("version"), str):
            continue
        digests = entry.get("sha256")
        if not isinstance(digests, dict):
            continue
        pins[str(name)] = BinaryPin(
            entry["version"], {str(key): str(value) for key, value in digests.items() if isinstance(value, str)}
        )
    return pins


_SSH_DEFAULTS: dict[str, Any] = {"ssh_user": "root", "ssh_port": 22}
"""Default panel SSH credentials, omitted from YAML when unchanged."""


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

    # Branding
    branding_dict = _serialize_dataclass(cfg.branding)
    for f in ("server_name", "icon", "color"):
        if branding_dict.get(f) == "":
            branding_dict.pop(f, None)
    if branding_dict:
        out["branding"] = branding_dict

    # Subscription page (only serialized once provisioning records it)
    if cfg.subscription_page is not None:
        sub_page_dict = _serialize_dataclass(cfg.subscription_page)
        # Only include deployed if True (keep YAML clean)
        if not sub_page_dict.get("deployed"):
            sub_page_dict.pop("deployed", None)
        if sub_page_dict:
            out["subscription_page"] = sub_page_dict

    # Telegram notifications (only serialized if configured)
    if cfg.telegram is not None:
        tg_dict = _serialize_dataclass(cfg.telegram)
        if tg_dict:
            out["telegram"] = tg_dict

    if cfg.connection_page_markers:
        out["connection_page_markers"] = list(cfg.connection_page_markers)
    if cfg.fleet_id:
        out["fleet_id"] = cfg.fleet_id
    if cfg.image_pins:
        out["image_pins"] = dict(sorted(cfg.image_pins.items()))
    if cfg.binary_pins:
        out["binary_pins"] = {
            name: {"version": pin.version, "sha256": dict(sorted(pin.sha256.items()))}
            for name, pin in sorted(cfg.binary_pins.items())
        }

    out.update(serialize_cluster_v3(cfg))

    # Extra fields (forward-compat)
    for k, v in cfg._extra.items():
        if k not in out:
            out[k] = v

    return out


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


def _load_string_list(data: dict[str, Any], key: str) -> list[str]:
    """Load one required string-list shape from typed cluster state."""
    raw = data.get(key, [])
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ValueError(f"{key} must be a list of strings")
    return list(raw)


def _load_cluster(data: dict[str, Any]) -> ClusterConfig:
    """Load a cluster config from parsed YAML."""
    # Panel
    panel = _load_dataclass(
        data.get("panel", {}),
        PanelConfig,
        _PANEL_FIELDS,
        defaults={**_SSH_DEFAULTS},
    )

    # Branding
    branding = _load_dataclass(
        data.get("branding", {}),
        BrandingConfig,
        _BRANDING_FIELDS,
        defaults={"server_name": "", "icon": "", "color": ""},
    )

    # Subscription page — absent or `null` means provisioning has not recorded it yet.
    subscription_page: SubscriptionPageConfig | None = None
    if data.get("subscription_page") is not None:
        subscription_page = _load_dataclass(
            data.get("subscription_page"),
            SubscriptionPageConfig,
            _SUBSCRIPTION_PAGE_FIELDS,
            defaults={"enabled": True},
            transforms={"enabled": bool},
        )

    # Telegram notifications — None = unconfigured (disabled in panel)
    telegram: TelegramConfig | None = None
    if data.get("telegram") is not None:
        telegram = _load_dataclass(
            data.get("telegram"),
            TelegramConfig,
            _public_fields(TelegramConfig),
        )

    connection_page_markers = _load_string_list(data, "connection_page_markers")
    raw_fleet_id = data.get("fleet_id")
    fleet_id = raw_fleet_id if isinstance(raw_fleet_id, str) else ""
    raw_pins = data.get("image_pins")
    image_pins = (
        {str(name): str(image) for name, image in raw_pins.items() if isinstance(image, str)}
        if isinstance(raw_pins, dict)
        else {}
    )
    binary_pins = _load_binary_pins(data.get("binary_pins"))
    v3 = load_cluster_v3(data)

    # Extra fields
    extra = {k: v for k, v in data.items() if k not in _KNOWN_TOP}

    return ClusterConfig(
        version=max(data.get("version", CURRENT_CLUSTER_VERSION), CURRENT_CLUSTER_VERSION),
        panel=panel,
        branding=branding,
        subscription_page=subscription_page,
        telegram=telegram,
        topology_intent=v3.topology_intent,
        workloads=v3.workloads,
        managed_bindings=v3.managed_bindings,
        allocations=v3.allocations,
        action_checkpoints=v3.action_checkpoints,
        connection_page_markers=connection_page_markers,
        fleet_id=fleet_id,
        image_pins=image_pins,
        binary_pins=binary_pins,
        active_generation=v3.active_generation,
        active_plan_hash=v3.active_plan_hash,
        pending_generation=v3.pending_generation,
        pending_plan_hash=v3.pending_plan_hash,
        _extra=extra,
    )


def _state_error(path: Path, reason: str) -> LocalStateCorruptedError:
    """Build the fail-closed error used for an unreadable existing state file."""
    return LocalStateCorruptedError(
        f"Cannot safely load {path}: {reason}.",
        hint=(
            f"Restore a known-good backup, or run Meridian recovery before changing the deployment. "
            f"Do not delete {path} unless you intentionally want to abandon its local state."
        ),
    )


def _require_mapping(data: dict[str, Any], key: str, *, nullable: bool = False) -> None:
    value = data.get(key)
    if value is None and nullable:
        return
    if key in data and not isinstance(value, dict):
        expected = "a mapping or null" if nullable else "a mapping"
        raise ValueError(f"{key} must be {expected}, got {type(value).__name__}")


def _validate_cluster_structure(data: dict[str, Any]) -> None:
    """Reject malformed known fields instead of coercing them into empty state."""
    version = data.get("version", CURRENT_CLUSTER_VERSION)
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError(f"version must be an integer, got {type(version).__name__}")

    for key in ("panel", "branding"):
        _require_mapping(data, key)
    for key in ("subscription_page", "telegram"):
        _require_mapping(data, key, nullable=True)

    validate_cluster_v3_structure(data)


def load_cluster(path: Path | None = None) -> ClusterConfig:
    """Load cluster.yml, treating only a missing file as fresh state."""
    if path is None:
        from meridian.config import CLUSTER_CONFIG

        path = CLUSTER_CONFIG
    logger.debug("Loading cluster config from %s", path)
    if not path.exists():
        return ClusterConfig()
    try:
        load_mtime_ns = path.stat().st_mtime_ns
        raw = path.read_text()
    except OSError as exc:
        raise LocalStateError(
            f"Cannot read {path}: {exc}.",
            hint="Check the file permissions and disk health, then retry.",
        ) from exc
    if not raw.strip():
        raise _state_error(path, "the file is empty")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise _state_error(path, f"the YAML is malformed ({e})") from e
    if not isinstance(data, dict):
        raise _state_error(path, f"the document root must be a mapping, got {type(data).__name__}")
    try:
        _validate_cluster_structure(data)
    except (TypeError, ValueError) as exc:
        raise _state_error(path, str(exc)) from exc
    version = data.get("version", CURRENT_CLUSTER_VERSION)
    if isinstance(version, int) and version > CURRENT_CLUSTER_VERSION:
        _load_warning(
            f"cluster.yml has version {version}, but this CLI only understands version {CURRENT_CLUSTER_VERSION}. "
            "Some fields may be ignored. Upgrade Meridian: pip install --upgrade meridian-vpn"
        )
    data.setdefault("version", CURRENT_CLUSTER_VERSION)
    try:
        cfg = _load_cluster(data)
    except (TypeError, ValueError) as exc:
        raise _state_error(path, str(exc)) from exc
    cfg._loaded_mtime_ns = load_mtime_ns

    # Mark future-version configs as read-only to prevent data loss
    if isinstance(version, int) and version > CURRENT_CLUSTER_VERSION:
        cfg._readonly = True

    errors = cfg.validate()
    if errors:
        preview = "; ".join(errors[:3])
        if len(errors) > 3:
            preview += f"; ... and {len(errors) - 3} more"
        raise _state_error(path, f"validation failed ({preview})")

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
        # Any change counts: a restored backup or copied file can carry an older mtime.
        if current_mtime_ns != cfg._loaded_mtime_ns:
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


def backup_cluster(path: Path | None = None) -> None:
    """Copy cluster.yml to cluster.yml.bak before mutations."""
    if path is None:
        from meridian.config import CLUSTER_CONFIG

        path = CLUSTER_CONFIG
    if path.exists():
        from meridian.config import CLUSTER_BACKUP

        shutil.copy2(str(path), str(CLUSTER_BACKUP))
