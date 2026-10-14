"""Shared SSH mock for all test layers.

``MockConnection`` is a pattern-matching stub for ``ServerConnection``.
Register expected responses with ``conn.when("pattern", stdout=..., rc=0)``;
first matching substring wins.  Unmatched commands return rc=0 with empty
stdout/stderr.

Provision tests consume it via the ``mock_conn`` fixture in
``tests/provision/conftest.py``.  Other test layers import directly::

    from tests.support.mock_connection import MockConnection
"""

from __future__ import annotations

import subprocess
from pathlib import PurePosixPath


class MockConnection:
    """Mock ServerConnection with pattern-matching command dispatch.

    Returns different results based on command substring matching.
    First matching pattern wins. Unmatched commands return the default
    (rc=0, stdout="", stderr="").

    Usage::

        conn = MockConnection()
        conn.when("dpkg-query", stdout="curl\\tok\\n")
        conn.when("apt-get install", rc=1, stderr="broken")
        result = step.run(conn, ctx)
    """

    def __init__(self) -> None:
        self._rules: list[tuple[str, subprocess.CompletedProcess[str]]] = []
        self._calls: list[str] = []
        self._run_kwargs: list[dict[str, object]] = []
        self._writes: list[tuple[str, bytes, dict[str, object]]] = []
        self._default = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        self.ip = "198.51.100.1"
        self.user = "root"
        self.local_mode = False
        self.needs_sudo = False

    def when(
        self,
        pattern: str,
        *,
        stdout: str = "",
        stderr: str = "",
        rc: int = 0,
    ) -> MockConnection:
        """Register a response for commands matching pattern (substring)."""
        self._rules.append(
            (
                pattern,
                subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr=stderr),
            )
        )
        return self

    def run(self, command: str, timeout: int = 30, **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Dispatch command to first matching pattern."""
        self._calls.append(command)
        self._run_kwargs.append(kwargs)
        for pattern, response in self._rules:
            if pattern in command:
                return response
        return self._default

    def _match_rule(self, text: str) -> subprocess.CompletedProcess[str] | None:
        """Return the first rule matching *text*, or None."""
        for pattern, response in self._rules:
            if pattern in text:
                return response
        return None

    def get_text(self, path: str, timeout: int = 30, **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Mock ServerConnection.get_text."""
        return self.run(f"cat {path} 2>/dev/null", timeout=timeout, **kwargs)

    def put_text(self, path: str, text: str, timeout: int = 30, **kwargs: object) -> subprocess.CompletedProcess[str]:
        """Mock ServerConnection.put_text.

        Records the write in ``_writes`` and dispatches through ``run()``
        with a synthetic ``cat > <path>`` command for pattern matching.
        Also checks ``put_bytes <path>`` and ``put_text <path>`` patterns
        before falling through to the ``cat >`` dispatch.
        """
        self._writes.append((path, text.encode(), kwargs))
        if kwargs.get("create_parent"):
            parent = str(PurePosixPath(path).parent)
            if parent and parent != ".":
                mkdir = self.run(f"mkdir -p {parent}", timeout=timeout)
                if mkdir.returncode != 0:
                    return mkdir
        # Check virtual put_bytes/put_text operations first
        hit = self._match_rule(f"put_bytes {path}") or self._match_rule(f"put_text {path}")
        if hit is not None:
            return hit
        return self.run(f"cat > {path}\n{text}", timeout=timeout)

    def put_bytes(
        self, path: str, data: bytes, timeout: int = 30, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        """Mock ServerConnection.put_bytes."""
        return self.put_text(path, data.decode(errors="replace"), timeout=timeout, **kwargs)

    @property
    def call_count(self) -> int:
        return len(self._calls)

    @property
    def calls(self) -> list[str]:
        return list(self._calls)

    @property
    def run_kwargs(self) -> list[dict[str, object]]:
        return list(self._run_kwargs)

    @property
    def writes(self) -> list[tuple[str, bytes, dict[str, object]]]:
        return list(self._writes)

    @property
    def write_map(self) -> dict[str, bytes]:
        """Map of remote path to last-written bytes."""
        return {path: data for path, data, _kwargs in self._writes}

    def assert_called_with_pattern(self, pattern: str) -> None:
        """Assert at least one call contained the given pattern."""
        assert any(pattern in c for c in self._calls), f"No call matching '{pattern}'. Calls: {self._calls}"

    def assert_not_called_with_pattern(self, pattern: str) -> None:
        """Assert no call contained the given pattern."""
        assert not any(pattern in c for c in self._calls), f"Unexpected call matching '{pattern}'. Calls: {self._calls}"
