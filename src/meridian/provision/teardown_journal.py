"""Private host-side retirement transaction. Standard library only, including on older controllers."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class JournalError(Exception):
    """A safe diagnostic: never include private config or keys."""


def sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def identity(path: Path) -> list[int]:
    item = path.lstat()
    bits = [item.st_dev, item.st_ino, item.st_mode]
    if not stat.S_ISDIR(item.st_mode):
        bits.extend((item.st_size, item.st_mtime_ns))
    return bits


@dataclass
class Retirement:
    parent: Path
    scope: str

    @property
    def tag(self) -> str:
        return hashlib.sha256(self.scope.encode()).hexdigest()[:24]

    @property
    def folder(self) -> Path:
        return self.parent / (".meridian-retirement-" + self.tag)

    def staged(self, original: Path) -> Path:
        # Sibling of the original even on separate volumes, outside ordinary nginx/unit globs.
        return original.with_name(".meridian-retired-" + self.tag + "-" + original.name)

    def read(self) -> dict[str, Any] | None:
        folder = self.folder
        if not os.path.lexists(folder):
            return None
        st = folder.lstat()
        if (
            folder.resolve() != folder
            or not stat.S_ISDIR(st.st_mode)
            or st.st_uid != os.geteuid()
            or st.st_mode & 0o077
        ):
            raise JournalError("The private teardown journal cannot be verified. No cleanup was attempted.")
        file = folder / "state.json"
        if not os.path.lexists(file):
            if not any(folder.iterdir()):
                return None
            raise JournalError("The teardown journal is incomplete. Inspect its private directory before retrying.")
        st = file.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077 or st.st_size > 524288:
            raise JournalError("The teardown journal cannot be verified.")
        data = json.loads(file.read_text())
        if data.get("scope") != self.tag or data.get("phase") not in {"preparing", "committed", "complete"}:
            raise JournalError("The previous teardown belongs to a different operation.")
        return data

    def write(self, data: dict[str, Any], phase: str) -> None:
        self.folder.mkdir(mode=0o700, exist_ok=True)
        self.read()  # never replace an unrecognized journal
        data = dict(data, phase=phase, scope=self.tag)
        fd, name = tempfile.mkstemp(dir=self.folder, prefix=".state-")
        try:
            with os.fdopen(fd, "w") as out:
                json.dump(data, out, separators=(",", ":"))
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, self.folder / "state.json")
            sync_dir(self.folder)
            sync_dir(self.parent)
        finally:
            Path(name).unlink(missing_ok=True)

    def clear(self) -> None:
        (self.folder / "state.json").unlink()
        self.folder.rmdir()
        sync_dir(self.parent)

    def plan(self, paths: list[Path]) -> dict[str, list[int]]:
        result = {}
        for path in dict.fromkeys(paths):
            if os.path.lexists(self.staged(path)):
                raise JournalError("A file already occupies private teardown staging. Nothing was removed.")
            if os.path.lexists(path):
                result[str(path)] = identity(path)
        return result

    def move(self, path: Path, signature: list[int]) -> None:
        dest = self.staged(path)
        if os.path.lexists(dest) or not os.path.lexists(path) or identity(path) != signature:
            raise JournalError("A reviewed file changed before removal. Nothing will overwrite it.")
        path.rename(dest)
        sync_dir(path.parent)

    def restore(self, artifacts: dict[str, list[int]]) -> None:
        # Check the whole set before moving anything back, never overwrite newer operator work.
        pending = []
        for raw, signature in artifacts.items():
            path = Path(raw)
            held = self.staged(path)
            if os.path.lexists(path) == os.path.lexists(held):
                raise JournalError(
                    "A staged file was replaced or is missing; inspect the private journal before retrying."
                )
            if os.path.lexists(held):
                if identity(held) != signature:
                    raise JournalError("A private staged file changed; nothing was overwritten.")
                pending.append((held, path))
            # If only the original exists, it was not staged or is already back.
            # An ACME renewal may have replaced it during review; leave that new file alone.
        for held, path in reversed(pending):
            held.rename(path)
            sync_dir(path.parent)

    def assert_no_replacement(self, artifacts: dict[str, list[int]]) -> None:
        if any(os.path.lexists(path) for path in artifacts):
            raise JournalError(
                "A retired path has been reused. Leave the new installation in place and inspect the journal."
            )

    def dispose(self, artifacts: dict[str, list[int]]) -> None:
        self.assert_no_replacement(artifacts)
        for raw, signature in artifacts.items():
            original = Path(raw)
            held = self.staged(original)
            if not os.path.lexists(held):
                continue  # previous cleanup removed just this object
            if identity(held) != signature:
                raise JournalError("A private staged file changed. Nothing will overwrite or erase it.")
            if held.is_symlink() or not held.is_dir():
                held.unlink()
            else:
                shutil.rmtree(held)
            sync_dir(held.parent)
