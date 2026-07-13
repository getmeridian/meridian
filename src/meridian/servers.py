"""Server registry — manages the ~/.meridian/servers.json index file."""

from __future__ import annotations

import builtins
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from meridian.core.servers import ServerConnectionDraft, ServerProfile, profile_from_draft

SERVER_REGISTRY_SCHEMA = "meridian.servers/v2"


@dataclass
class ServerEntry:
    """A known server: host, SSH user, display name, and SSH port."""

    host: str
    user: str = "root"
    name: str = ""
    port: int = 22
    key_path: str = ""

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
            )
        return None

    def add(self, entry: ServerEntry) -> None:
        """Add a server, deduplicating by host IP."""
        existing_profile = self._store.find(entry.host) or (self._store.find(entry.name) if entry.name else None)
        draft = ServerConnectionDraft(
            title=entry.name or entry.host,
            host=entry.host,
            ssh_user=entry.user,
            ssh_port=entry.port,
        )
        profile = profile_from_draft(draft)
        preserved_key_path = entry.key_path or (existing_profile.key_path if existing_profile else "")
        if preserved_key_path:
            profile = profile.model_copy(
                update={
                    "auth_state": existing_profile.auth_state if existing_profile else profile.auth_state,
                    "key_path": preserved_key_path,
                    "last_error": existing_profile.last_error if existing_profile else profile.last_error,
                    "last_validated_at": existing_profile.last_validated_at
                    if existing_profile
                    else profile.last_validated_at,
                    "source": existing_profile.source if existing_profile else profile.source,
                }
            )
        self._store.upsert(profile)

    def remove(self, query: str) -> bool:
        """Remove a server by IP or name. Returns True if found and removed."""
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
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_profiles = payload.get("servers", []) if isinstance(payload, dict) else []
    profiles: builtins.list[ServerProfile] = []
    for raw in raw_profiles:
        try:
            profiles.append(ServerProfile.model_validate(raw))
        except ValidationError:
            continue
    return profiles


def _profile_entries(profiles: list[ServerProfile]) -> list[ServerEntry]:
    return [
        ServerEntry(
            host=profile.host,
            user=profile.ssh_user,
            name=profile.title,
            port=profile.ssh_port,
            key_path=profile.key_path,
        )
        for profile in profiles
    ]
