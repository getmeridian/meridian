"""Process renderers for Meridian CLI adapters."""

from __future__ import annotations

import sys
from typing import IO, Any

from meridian.core.output import json_dumps, jsonl_dumps


def emit_json(value: Any, *, stream: IO[str] | None = None, reveal: frozenset[str] = frozenset()) -> None:
    """Write a model or plain value as formatted JSON (``reveal``: keys kept verbatim)."""
    target = stream or sys.stdout
    target.write(json_dumps(value, reveal=reveal) + "\n")
    target.flush()


def emit_jsonl(value: Any, *, stream: IO[str] | None = None) -> None:
    """Write a model or plain value as one compact JSONL record."""
    target = stream or sys.stdout
    target.write(jsonl_dumps(value) + "\n")
    target.flush()
