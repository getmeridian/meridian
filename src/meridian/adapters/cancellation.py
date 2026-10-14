"""File-backed cancellation channel between the Engine and a deploy process."""

from __future__ import annotations

from pathlib import Path


class FileCancellationToken:
    """Cancellation token whose request is the existence of one marker file.

    The Engine owns the private temporary directory and creates the file; the
    child `meridian deploy --cancel-file PATH` only polls for it at safe
    points. No signal ever reaches the child, so SSH subprocesses are never
    interrupted mid-command.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def is_cancelled(self) -> bool:
        return self.path.exists()

    def request(self) -> None:
        """Create the marker; a no-op once the process directory is gone."""
        try:
            self.path.touch(exist_ok=True)
        except OSError:
            pass
