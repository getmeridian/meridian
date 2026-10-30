"""Never lose a fleet with its control server (#95): device keys, snapshot pulls, and restore."""

from __future__ import annotations

from meridian.core.errors import MeridianError


class BackupError(MeridianError):
    """A backup or restore step that could not finish; the message says what to do."""
