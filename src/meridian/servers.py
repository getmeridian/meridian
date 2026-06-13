"""Server registry — manages the ~/.meridian/servers.json index file.

JSON-only persistence. Legacy text file (``servers``) is automatically
migrated to ``servers.json`` on first load.
"""

from __future__ import annotations

import builtins
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from meridian.core.servers import ServerConnectionDraft, ServerProfile, profile_from_draft

logger = logging.getLogger(__name__)

SERVER_ROLE_EXIT = "exit"
SERVER_ROLE_RELAY = "relay"
_SERVER_ROLES = {SERVER_ROLE_EXIT, SERVER_ROLE_RELAY}
SERVER_REGISTRY_SCHEMA = "meridian.servers/v2"


@dataclass
class ServerEntry:
    """A known server: host, SSH user, display name, and SSH port."""

    host: str
    user: str = "root"
    name: str = ""
    role: str = SERVER_ROLE_EXIT
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

    def __str__(self) -> str:
        parts = [self.host, self.user]
        if self.role == SERVER_ROLE_EXIT:
            if self.name:
                parts.append(self.name)
            if self.port != 22:
                if not self.name:
                    parts.append("-")
                parts.append(f"port={self.port}")
            return " ".join(parts)

        parts.extend([self.name or "-", self.role])
        if self.port != 22:
            parts.append(f"port={self.port}")
        return " ".join(parts)

    @classmethod
    def from_line(cls, line: str) -> ServerEntry | None:
        """Parse a line from the legacy servers file. Returns None for comments/blanks."""
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return None
        parts = stripped.split()
        if len(parts) < 2:
            return None

        port = 22
        if parts[-1].startswith("port="):
            try:
                port = int(parts[-1].split("=", 1)[1])
            except (ValueError, IndexError):
                pass
            parts = parts[:-1]

        if len(parts) >= 4 and parts[3] in _SERVER_ROLES:
            name = "" if parts[2] == "-" else parts[2]
            return cls(host=parts[0], user=parts[1], name=name, role=parts[3], port=port)
        name = parts[2] if len(parts) > 2 else ""
        if name == "-":
            name = ""
        return cls(host=parts[0], user=parts[1], name=name, port=port)


def _migrate_legacy_file(legacy_path: Path, json_path: Path) -> None:
    """One-shot migration: convert legacy text file to servers.json.

    Only runs when the legacy file exists and servers.json does not.
    After migration, the legacy file is left in place (read-only backup).
    """
    if not legacy_path.exists() or json_path.exists():
        return

    entries: list[ServerEntry] = []
    for raw in legacy_path.read_text().splitlines():
        entry = ServerEntry.from_line(raw)
        if entry:
            entries.append(entry)

    if not entries:
        return

    profiles: list[ServerProfile] = []
    for entry in entries:
        draft = ServerConnectionDraft(
            title=entry.name or entry.host,
            host=entry.host,
            ssh_user=entry.user,
            ssh_port=entry.port,
        )
        profiles.append(profile_from_draft(draft, source="legacy"))

    store = ServerProfileStore(json_path)
    for profile in profiles:
        store.upsert(profile)

    logger.info("Migrated %d servers from legacy text file to servers.json", len(entries))


class ServerRegistry:
    """CRUD operations on the servers index file.

    All persistence is through servers.json via ServerProfileStore.
    Legacy text file is auto-migrated on first access.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._json_path = path.with_suffix(".json")
        _migrate_legacy_file(path, self._json_path)
        self._store = ServerProfileStore(self._json_path)

    def _read_legacy_entries(self) -> list[ServerEntry]:
        """Read legacy text entries (used only for migration)."""
        entries: list[ServerEntry] = []
        if not self.path.exists():
            return entries
        for raw in self.path.read_text().splitlines():
            entry = ServerEntry.from_line(raw)
            if entry:
                entries.append(entry)
        return entries

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
        # Also update legacy file for backward compat during transition
        if self.path.exists():
            lines = self.path.read_text().splitlines()
            new_lines = [
                raw
                for raw in lines
                if not (ServerEntry.from_line(raw) and ServerEntry.from_line(raw).host == entry.host)
            ]
            new_lines.append(str(entry))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("\n".join(new_lines) + "\n" if new_lines else "")

        if entry.role != SERVER_ROLE_EXIT:
            return

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
        # Also clean legacy file for backward compat
        removed_legacy = False
        if self.path.exists():
            lines = self.path.read_text().splitlines()
            new_lines = []
            for raw in lines:
                existing = ServerEntry.from_line(raw)
                if existing and (existing.host == query or existing.name == query):
                    removed_legacy = True
                    continue
                new_lines.append(raw)
            if removed_legacy:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text("\n".join(new_lines) + "\n" if new_lines else "")

        removed_profile = self._store.remove(query)
        return removed_legacy or removed_profile


class ServerProfileStore:
    """JSON server profile registry for Studio and future Engine flows."""

    def __init__(self, path: Path, *, legacy_path: Path | None = None) -> None:
        self.path = path
        self.legacy_path = legacy_path

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
