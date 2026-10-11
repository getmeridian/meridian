"""Pre-deploy check that public ports 443 and 80 are free for Meridian."""

from __future__ import annotations

import re
import shlex

from meridian.console import choose, err_console, fail, is_quiet_mode, warn
from meridian.ssh import ServerConnection


def check_ports(conn: ServerConnection, ip: str, yes: bool) -> None:
    """Check that ports 443 and 80 are available before deploying.

    Allows re-deploy over existing Meridian processes.
    Loops with retry prompt if a non-Meridian process holds a port.
    """
    allowed = {"nginx", "xray", "remnawave", "remnawave-node", "docker-proxy"}
    # HAProxy/Caddy count as ours only when an older Meridian release installed them.
    legacy_markers = {
        "haproxy": "grep -qs '^# HAProxy SNI Router' /etc/haproxy/haproxy.cfg",
        "caddy": "test -e /etc/caddy/conf.d/meridian.caddy",
    }

    for port in (443, 80):
        while True:
            q_port = shlex.quote(str(port))
            result = conn.run(f"ss -tlnp sport = :{q_port} 2>/dev/null | grep LISTEN", timeout=10)
            if not result.stdout.strip():
                break  # port free

            match = re.search(r'users:\(\("([^"]*)"', result.stdout)
            proc = match.group(1) if match else "unknown"
            if proc in allowed:
                break  # Meridian's own process -- OK for re-deploy
            if proc in legacy_markers and conn.run(legacy_markers[proc], timeout=10).returncode == 0:
                break  # retired by InstallNginx

            warn(f"Port {port} is in use by {proc}")
            if not is_quiet_mode():
                err_console.print(f"  [dim]Port {port} must be free for Meridian.[/dim]")
                err_console.print(f"  [dim]Stop {proc} and retry, or press Ctrl+C to abort.[/dim]")
                err_console.print()

            if yes:
                fail(
                    f"Port {port} is occupied by {proc}",
                    hint=f"Stop {proc} first: sudo systemctl stop {proc}",
                    hint_type="user",
                )

            choice = choose("Retry?", ["Yes", "No"])
            if choice == 2:
                fail("Aborted -- port conflict", hint_type="user")
