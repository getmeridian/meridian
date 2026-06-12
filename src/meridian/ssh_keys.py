"""Lightweight SSH host-key utilities (stdlib-only).

No Rich, no console, no heavy imports — safe for use from both the CLI
(``meridian.ssh``) and the Engine (``meridian.engine``) without pulling
in terminal rendering.
"""

from __future__ import annotations

import subprocess


def host_key_known(host: str, port: int = 22) -> bool:
    """Check if the host key for *host* is already in ``known_hosts``."""
    lookup = host_key_lookup(host, port)
    try:
        result = subprocess.run(
            ["ssh-keygen", "-F", lookup],
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def host_key_lookup(host: str, port: int = 22) -> str:
    """Return the OpenSSH ``known_hosts`` lookup string for a host/port.

    Non-default ports use ``[host]:port`` notation, matching ``ssh-keygen -F``.
    """
    return f"[{host}]:{port}" if port != 22 else host
