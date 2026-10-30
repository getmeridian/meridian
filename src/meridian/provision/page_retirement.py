"""Retire Meridian's SSH-published page artifacts after the new nginx config is live."""

from __future__ import annotations

import shlex

from meridian.ssh import CommandResult, ServerConnection

_ROOT = "/var/www/private"
_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


def retire_page_artifacts(conn: ServerConnection) -> CommandResult:
    """Delete immediate UUID directories and pwa; preserve other files in the private root."""
    root = shlex.quote(_ROOT)
    pattern = shlex.quote(f"{_ROOT}/({_UUID}|pwa)")
    return conn.run(
        f"if test -d {root}; then find {root} -mindepth 1 -maxdepth 1 -type d "
        f"-regextype posix-extended -regex {pattern} -exec rm -rf -- '{{}}' +; fi",
        timeout=30,
    )
