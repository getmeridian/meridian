"""An in-memory control server that answers the SSH apply fence's read and compare-and-swap commands."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
from typing import Any

from meridian.state_fence import FENCE_FILE


class FenceHost:
    """In-memory control server that honours the read and compare-and-swap commands."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.swaps = 0
        self.swap_calls = 0
        self.before_swap: Any = None
        # Connection drops: the next N reads fail, and swaps fail before (lost) or after (landed) moving.
        self.dropped_reads = 0
        self.lost_swaps = 0
        self.landed_swaps = 0
        self.on_lost_swap: Any = None

    def run(self, command: str, timeout: int = 30, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command.startswith("flock"):
            self.swap_calls += 1
            if self.lost_swaps:
                self.lost_swaps -= 1
                if self.on_lost_swap is not None:
                    self.on_lost_swap()
                return subprocess.CompletedProcess([], 255, "", "Connection reset by peer")
            result = self._swap(command)
            if self.landed_swaps and result.returncode == 0:
                self.landed_swaps -= 1
                return subprocess.CompletedProcess([], 124, "", f"Command timed out after {timeout}s")
            return result
        if "sha256sum" in command:
            if self.dropped_reads:
                self.dropped_reads -= 1
                return subprocess.CompletedProcess([], 255, "", "Connection timed out during banner exchange")
            body = self.files.get(FENCE_FILE)
            out = "absent\n" if body is None else f"{_digest(body)}\n{body}"
            return subprocess.CompletedProcess([], 0, out, "")
        raise AssertionError(f"unexpected command: {command}")

    def put_text(self, path: str, text: str, timeout: int = 30, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs.get("mode") == "600" and kwargs.get("sensitive") is True
        self.files[path] = text
        return subprocess.CompletedProcess([], 0, "", "")

    def _swap(self, command: str) -> subprocess.CompletedProcess[str]:
        if self.before_swap is not None:
            hook, self.before_swap = self.before_swap, None
            hook()
        script = shlex.split(command)[-1]
        expected = re.search(r'\[ "\$cur" = (\S+) \]', script)
        staged = re.search(r"mv (\S+) ", script)
        assert expected and staged
        current = self.files.get(FENCE_FILE)
        if (current is None and expected.group(1) != "absent") or (
            current is not None and _digest(current) != expected.group(1)
        ):
            self.files.pop(staged.group(1), None)
            return subprocess.CompletedProcess([], 4, "", "")
        self.files[FENCE_FILE] = self.files.pop(staged.group(1))
        self.swaps += 1
        return subprocess.CompletedProcess([], 0, "", "")

    def record(self) -> dict[str, Any]:
        return json.loads(self.files[FENCE_FILE])


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
