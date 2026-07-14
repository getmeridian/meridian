"""Server registry — manages the ~/.meridian/servers.json index file."""

from __future__ import annotations

import builtins
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from meridian.core.errors import LocalStateCorruptedError, LocalStateError
from meridian.core.servers import (
    ServerAuthState,
    ServerConnectionDraft,
    ServerProfile,
    ServerSource,
    profile_from_draft,
)

if TYPE_CHECKING:
    from meridian.cluster import ClusterConfig

SERVER_REGISTRY_SCHEMA = "meridian.servers/v3"
_SUPPORTED_SERVER_REGISTRY_SCHEMAS = {"meridian.servers/v2", SERVER_REGISTRY_SCHEMA}


@dataclass
class ServerEntry:
    """A known server: host, SSH user, display name, and SSH port."""

    host: str
    user: str = "root"
    name: str = ""
    port: int = 22
    key_path: str = ""
    id: str = ""
    auth_state: ServerAuthState = "unknown"
    last_validated_at: str = ""
    last_error: str = ""
    source: ServerSource = "manual"

    @property
    def ssh_user(self) -> str:
        """Alias for ``user`` — matches ``ServerProfile.ssh_user``."""
        return self.user

    @property
    def ssh_port(self) -> int:
        """Alias for ``port`` — matches ``ServerProfile.ssh_port``."""
        return self.port

    @property
    def title(self) -> str:
        """Alias for ``name`` — matches ``ServerProfile.title``."""
        return self.name


class ServerRegistry:
    """CLI-facing adapter over the JSON server profile store."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._store = ServerProfileStore(path)

    def list(self) -> list[ServerEntry]:
        """Return all registered servers from JSON store."""
        return _profile_entries(self._store.list())

    def count(self) -> int:
        return len(self.list())

    def find(self, query: str) -> ServerEntry | None:
        """Find a server by IP, name, or v2 profile ID (first match)."""
        needle = query.strip()
        profile = self._store.find(needle)
        if profile:
            return ServerEntry(
                host=profile.host,
                user=profile.ssh_user,
                name=profile.title,
                port=profile.ssh_port,
                key_path=profile.key_path,
                id=profile.id,
                auth_state=profile.auth_state,
                last_validated_at=profile.last_validated_at,
                last_error=profile.last_error,
                source=profile.source,
            )
        return None

    def add(self, entry: ServerEntry) -> None:
        """Add a server, deduplicating by host IP."""
        existing_profile = (
            (self._store.find(entry.id) if entry.id else None)
            or self._store.find(entry.host)
            or (self._store.find(entry.name) if entry.name else None)
        )
        draft = ServerConnectionDraft(
            title=entry.name or entry.host,
            host=entry.host,
            ssh_user=entry.user,
            ssh_port=entry.port,
        )
        profile = profile_from_draft(
            draft,
            auth_state=entry.auth_state,
            source=entry.source,
            profile_id=existing_profile.id if existing_profile else entry.id,
        )
        preserved_key_path = entry.key_path or (existing_profile.key_path if existing_profile else "")
        existing_auth = existing_profile.auth_state if existing_profile else "unknown"
        auth_state = _stronger_auth_state(existing_auth, entry.auth_state)
        profile = profile.model_copy(
            update={
                "auth_state": auth_state,
                "key_path": preserved_key_path,
                "last_error": entry.last_error or (existing_profile.last_error if existing_profile else ""),
                "last_validated_at": entry.last_validated_at
                or (existing_profile.last_validated_at if existing_profile else ""),
                "source": existing_profile.source if existing_profile else entry.source,
            }
        )
        self._store.upsert(profile)

    def remove(self, query: str, *, cluster: ClusterConfig | None = None) -> bool:
        """Remove an unreferenced server by ID, IP, or name."""
        profile = self._store.find(query)
        if profile is not None and cluster is not None:
            references = _cluster_references(profile, cluster)
            if references:
                joined = ", ".join(references)
                raise LocalStateError(
                    f"Server '{profile.title}' is still used by {joined}.",
                    hint="Remove or reassign those deployment roles before deleting the saved server.",
                    category="user",
                )
        return self._store.remove(query)


class ServerProfileStore:
    """JSON server profile registry for CLI and Engine flows."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def list(self) -> list[ServerProfile]:
        """Return saved server profiles from JSON only."""
        return _read_profiles_file(self.path)

    def find(self, query: str) -> ServerProfile | None:
        """Find a server by stable ID, title, or host."""
        needle = query.strip()
        for profile in self.list():
            if profile.id == needle or profile.title == needle or profile.host == needle:
                return profile
        return None

    def upsert(self, profile: ServerProfile) -> None:
        """Insert or replace a profile by stable ID, title, or host."""
        existing = next(
            (
                candidate
                for candidate in self.list()
                if candidate.id == profile.id or candidate.title == profile.title or candidate.host == profile.host
            ),
            None,
        )
        if existing is not None and existing.id != profile.id:
            profile = profile.model_copy(update={"id": existing.id})
        profiles = [
            existing
            for existing in self.list()
            if existing.id != profile.id and existing.title != profile.title and existing.host != profile.host
        ]
        profiles.append(profile)
        self._write_profiles(profiles)

    def remove(self, query: str) -> bool:
        """Remove a profile by stable ID, title, or host."""
        needle = query.strip()
        profiles = self.list()
        remaining = [
            profile
            for profile in profiles
            if profile.id != needle and profile.title != needle and profile.host != needle
        ]
        if len(remaining) == len(profiles):
            return False
        self._write_profiles(remaining)
        return True

    def _write_profiles(self, profiles: builtins.list[ServerProfile]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.path.parent.chmod(0o700)
        except PermissionError:
            pass
        payload: dict[str, Any] = {
            "schema": SERVER_REGISTRY_SCHEMA,
            "servers": [profile.model_dump(mode="json") for profile in profiles],
        }
        data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            os.write(fd, data)
            os.fsync(fd)
            os.close(fd)
            fd = -1
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            if fd >= 0:
                os.close(fd)
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise


def _read_profiles_file(path: Path) -> builtins.list[ServerProfile]:
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LocalStateError(
            f"Cannot read {path}: {exc}.",
            hint="Check the file permissions and disk health, then retry.",
        ) from exc
    if not raw.strip():
        raise _server_state_error(path, "the file is empty")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _server_state_error(path, f"the JSON is malformed ({exc})") from exc
    if not isinstance(payload, dict):
        raise _server_state_error(path, f"the document root must be an object, got {type(payload).__name__}")
    schema = payload.get("schema")
    if schema not in _SUPPORTED_SERVER_REGISTRY_SCHEMAS:
        raise _server_state_error(path, f"unsupported or missing schema {schema!r}")
    raw_profiles = payload.get("servers")
    if not isinstance(raw_profiles, list):
        raise _server_state_error(path, f"servers must be an array, got {type(raw_profiles).__name__}")
    profiles: builtins.list[ServerProfile] = []
    for index, item in enumerate(raw_profiles):
        try:
            profiles.append(ServerProfile.model_validate(item))
        except ValidationError as exc:
            raise _server_state_error(path, f"servers[{index}] is invalid ({exc.errors()[0]['msg']})") from exc
    _validate_profile_uniqueness(path, profiles)
    return profiles


def _profile_entries(profiles: list[ServerProfile]) -> list[ServerEntry]:
    return [
        ServerEntry(
            host=profile.host,
            user=profile.ssh_user,
            name=profile.title,
            port=profile.ssh_port,
            key_path=profile.key_path,
            id=profile.id,
            auth_state=profile.auth_state,
            last_validated_at=profile.last_validated_at,
            last_error=profile.last_error,
            source=profile.source,
        )
        for profile in profiles
    ]


def _server_state_error(path: Path, reason: str) -> LocalStateCorruptedError:
    return LocalStateCorruptedError(
        f"Cannot safely load {path}: {reason}.",
        hint=(
            "Restore a known-good servers.json file or repair it explicitly. "
            "Meridian will not silently discard saved server records."
        ),
    )


def _validate_profile_uniqueness(path: Path, profiles: list[ServerProfile]) -> None:
    for field_name in ("id", "title", "host"):
        seen: set[str] = set()
        for profile in profiles:
            value = getattr(profile, field_name)
            if value in seen:
                raise _server_state_error(path, f"duplicate server {field_name} {value!r}")
            seen.add(value)


def _stronger_auth_state(current: ServerAuthState, requested: ServerAuthState) -> ServerAuthState:
    rank: dict[ServerAuthState, int] = {
        "failed": 0,
        "unknown": 1,
        "validated": 2,
        "key_ready": 3,
    }
    return requested if rank[requested] >= rank[current] else current


def _cluster_references(profile: ServerProfile, cluster: ClusterConfig) -> list[str]:
    references: list[str] = []
    if cluster.panel.server_ip == profile.host:
        references.append("the control plane")
    references.extend(f"node '{node.name or node.ip}'" for node in cluster.nodes if node.ip == profile.host)
    references.extend(f"relay '{relay.name or relay.ip}'" for relay in cluster.relays if relay.ip == profile.host)
    return references
