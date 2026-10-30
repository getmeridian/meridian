"""File-backed cancellation channel for a machine-mode deploy process."""

from __future__ import annotations

from pathlib import Path


class FileCancellationToken:
    """Cancellation token whose request is the existence of one marker file.

    The caller creates the file; `meridian deploy --cancel-file PATH` only polls
    for it at safe points. No signal ever reaches the process, so SSH
    subprocesses are never interrupted mid-command.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def is_cancelled(self) -> bool:
        return self.path.exists()
