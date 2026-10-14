"""Minimal server connection protocols for core-facing callers."""

from __future__ import annotations

from typing import Protocol


class ServerCommandResult(Protocol):
    """Lightweight command-result surface for server onboarding probes."""

    returncode: int
    stdout: str
    stderr: str


class ServerConnection(Protocol):
    """Lightweight SSH connection surface for server onboarding probes.

    Covers only the ``conn.run()`` subset needed by server validation, key
    bootstrap, OS detection, and diagnostics.
    """

    def run(
        self,
        command: str,
        timeout: int = 30,
        *,
        sudo: bool | None = None,
        sensitive: bool = False,
    ) -> ServerCommandResult:
        """Run a command on the server."""
