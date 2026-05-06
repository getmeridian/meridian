"""Server registry — manages the ~/.meridian/servers index file."""

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

    def __str__(self) -> str:
        parts = [self.host, self.user]
        if self.role == SERVER_ROLE_EXIT:
            if self.name:
                parts.append(self.name)
            # Append port only when non-default (backwards-compatible)
            if self.port != 22:
                if not self.name:
                    parts.append("-")
                parts.append(f"port={self.port}")
            return " ".join(parts)

        # Relay entries need an explicit role marker so fresh machines can
        # distinguish them from exit servers without relying on local cache.
        parts.extend([self.name or "-", self.role])
        if self.port != 22:
            parts.append(f"port={self.port}")
        return " ".join(parts)

    @classmethod
    def from_line(cls, line: str) -> ServerEntry | None:
        """Parse a line from the servers file. Returns None for comments/blanks."""
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return None
        parts = stripped.split()
        if len(parts) < 2:
            return None

        # Extract port=N from the end if present
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
        # Handle the "-" placeholder for name when port was present
        if name == "-":
            name = ""
        return cls(host=parts[0], user=parts[1], name=name, port=port)


class ServerRegistry:
    """CRUD operations on the servers index file.

    File format: one server per line, space-separated: "host user [name]"
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read_lines(self) -> list[str]:
        if not self.path.exists():
            return []
        return self.path.read_text().splitlines()

    def _write_lines(self, lines: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(lines) + "\n" if lines else "")

    def _read_legacy_entries(self) -> list[ServerEntry]:
        entries: list[ServerEntry] = []
        for raw in self._read_lines():
            entry = ServerEntry.from_line(raw)
            if entry:
                entries.append(entry)
        return entries

    def list(self) -> list[ServerEntry]:
        """Return all registered servers."""
        entries = self._read_legacy_entries()
        return _merge_server_entries(entries, _profile_entries(_read_profiles_file(self.path.with_suffix(".json"))))

    def count(self) -> int:
        return len(self.list())

    def find(self, query: str) -> ServerEntry | None:
        """Find a server by IP, name, or v2 profile ID (first match)."""
        needle = query.strip()
        for profile in _read_profiles_file(self.path.with_suffix(".json")):
            if profile.id == needle or profile.title == needle or profile.host == needle:
                return ServerEntry(
                    host=profile.host,
                    user=profile.ssh_user,
                    name=profile.title,
                    port=profile.ssh_port,
                    key_path=profile.key_path,
                )
        for entry in self.list():
            if entry.host == needle or entry.name == needle:
                return entry
        return None

    def add(self, entry: ServerEntry) -> None:
        """Add a server, deduplicating by host IP."""
        lines = self._read_lines()
        # Remove existing entry for same host
        new_lines = []
        for raw in lines:
            existing = ServerEntry.from_line(raw)
            if existing and existing.host == entry.host:
                continue
            new_lines.append(raw)
        new_lines.append(str(entry))
        self._write_lines(new_lines)
        if entry.role != SERVER_ROLE_EXIT:
            return
        profile_store = ServerProfileStore(self.path.with_suffix(".json"), legacy_path=self.path)
        existing_profile = profile_store.find(entry.host) or (profile_store.find(entry.name) if entry.name else None)
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
        profile_store.upsert(profile)

    def remove(self, query: str) -> bool:
        """Remove a server by IP or name. Returns True if found and removed."""
        lines = self._read_lines()
        new_lines = []
        removed = False
        for raw in lines:
            existing = ServerEntry.from_line(raw)
            if existing and (existing.host == query or existing.name == query):
                removed = True
                continue
            new_lines.append(raw)
        if removed:
            self._write_lines(new_lines)
        removed_profile = ServerProfileStore(self.path.with_suffix(".json"), legacy_path=self.path).remove(query)
        return removed or removed_profile


class ServerProfileStore:
    """JSON server profile registry for Studio and future Engine flows."""

    def __init__(self, path: Path, *, legacy_path: Path | None = None) -> None:
        self.path = path
        self.legacy_path = legacy_path

    def list(self) -> list[ServerProfile]:
        """Return saved server profiles, migrating readable legacy entries in memory."""
        profiles: list[ServerProfile] = []
        if self.legacy_path and self.legacy_path.exists():
            profiles.extend(self._legacy_profiles())
        if self.path.exists():
            profiles.extend(self._read_profiles())
        return _merge_profiles(profiles)

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

    def _read_profiles(self) -> builtins.list[ServerProfile]:
        return _read_profiles_file(self.path)

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

    def _legacy_profiles(self) -> builtins.list[ServerProfile]:
        assert self.legacy_path is not None
        profiles: builtins.list[ServerProfile] = []
        for entry in ServerRegistry(self.legacy_path)._read_legacy_entries():
            draft = ServerConnectionDraft(
                title=entry.name or entry.host,
                host=entry.host,
                ssh_user=entry.user,
                ssh_port=entry.port,
            )
            profiles.append(profile_from_draft(draft, source="legacy"))
        return profiles


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


def _merge_server_entries(entries: list[ServerEntry], extra: list[ServerEntry]) -> list[ServerEntry]:
    merged: list[ServerEntry] = []
    for entry in [*entries, *extra]:
        merged = [
            existing
            for existing in merged
            if existing.host != entry.host and (not entry.name or existing.name != entry.name)
        ]
        merged.append(entry)
    return merged


def _merge_profiles(profiles: list[ServerProfile]) -> list[ServerProfile]:
    merged: list[ServerProfile] = []
    for profile in profiles:
        merged = [
            existing
            for existing in merged
            if existing.id != profile.id and existing.title != profile.title and existing.host != profile.host
        ]
        merged.append(profile)
    return merged
