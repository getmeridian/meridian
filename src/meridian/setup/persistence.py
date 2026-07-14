"""Atomic persistence for the secret-free setup draft."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from meridian.core.errors import LocalStateCorruptedError, LocalStateError
from meridian.core.setup import SetupDraft


class SetupDraftStore:
    """Load and atomically save one resumable setup draft."""

    def __init__(self, path: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self.path = path
        self._clock = clock or (lambda: datetime.now(UTC))

    def load(self) -> SetupDraft | None:
        """Return None only when no draft has ever been created."""
        if not self.path.exists():
            return None
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LocalStateError(
                f"Cannot read {self.path}: {exc}.",
                hint="Check file permissions and disk health, then retry setup.",
            ) from exc
        if not raw.strip():
            raise self._corrupted("the file is empty")
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise self._corrupted(f"the JSON is malformed ({exc})") from exc
        if not isinstance(payload, dict):
            raise self._corrupted(f"the document root must be an object, got {type(payload).__name__}")
        try:
            return SetupDraft.model_validate(payload)
        except ValidationError as exc:
            detail = exc.errors()[0]
            location = ".".join(str(part) for part in detail["loc"])
            raise self._corrupted(f"{location or 'draft'} is invalid ({detail['msg']})") from exc

    def save(self, draft: SetupDraft) -> SetupDraft:
        """Validate and durably replace the draft with mode 0600."""
        timestamp = self._clock().astimezone(UTC).isoformat().replace("+00:00", "Z")
        persisted = SetupDraft.model_validate(
            draft.model_copy(update={"updated_at": timestamp}).model_dump(mode="json", by_alias=True)
        )
        payload = json.dumps(
            persisted.model_dump(mode="json", by_alias=True),
            indent=2,
            sort_keys=True,
        ).encode("utf-8") + b"\n"

        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.path.parent.chmod(0o700)
        except PermissionError:
            pass

        fd, temporary_path = tempfile.mkstemp(dir=str(self.path.parent), prefix=".setup-", suffix=".tmp")
        try:
            os.write(fd, payload)
            os.fsync(fd)
            os.close(fd)
            fd = -1
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.path)
        except BaseException:
            if fd >= 0:
                os.close(fd)
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)
            raise
        return persisted

    def delete(self) -> bool:
        """Remove a completed or explicitly discarded draft."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise LocalStateError(
                f"Cannot remove {self.path}: {exc}.",
                hint="Check file permissions, then retry.",
            ) from exc
        return True

    def _corrupted(self, reason: str) -> LocalStateCorruptedError:
        return LocalStateCorruptedError(
            f"Cannot safely resume {self.path}: {reason}.",
            hint=(
                "Restore a known-good setup.json or move the damaged file aside explicitly. "
                "Meridian will not discard setup progress automatically."
            ),
        )
