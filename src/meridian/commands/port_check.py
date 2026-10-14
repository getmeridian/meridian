"""Pre-deploy check that the ports Meridian's nginx binds are free."""

from __future__ import annotations

import shlex

from meridian.console import choose, err_console, fail, is_quiet_mode, warn
from meridian.core.defaults import CONTROL_PROXY_TLS_PORT, CONTROL_TLS_PORT
from meridian.diagnostics.preflight import MERIDIAN_LISTENERS, listener_process
from meridian.ssh import ServerConnection


def check_ports(conn: ServerConnection, yes: bool) -> None:
    """Check that public 443/80 and nginx's internal TLS ports are available before deploying.

    Allows re-deploy over existing Meridian processes.
    Loops with retry prompt if a non-Meridian process holds a port.
    """
    # HAProxy/Caddy count as ours only when an older Meridian release installed them.
    legacy_markers = {
        "haproxy": "grep -qs '^# HAProxy SNI Router' /etc/haproxy/haproxy.cfg",
        "caddy": "test -e /etc/caddy/conf.d/meridian.caddy",
    }

    # The panel's TLS server listens on these loopback ports; a taken one fails `nginx -t` mid-deploy.
    internal = (CONTROL_TLS_PORT, CONTROL_PROXY_TLS_PORT)

    for port in (443, 80, *internal):
        while True:
            q_port = shlex.quote(str(port))
            result = conn.run(f"ss -tlnp sport = :{q_port} 2>/dev/null | grep LISTEN", timeout=10)
            if not result.stdout.strip():
                break  # port free

            proc = listener_process(result.stdout)
            if proc in MERIDIAN_LISTENERS:
                break  # Meridian's own process -- OK for re-deploy
            if proc in legacy_markers and conn.run(legacy_markers[proc], timeout=10).returncode == 0:
                break  # retired by InstallNginx

            warn(f"Port {port} is in use by {proc}")
            if not is_quiet_mode():
                if port in internal:
                    err_console.print(f"  [dim]Meridian's nginx serves the panel on 127.0.0.1:{port}.[/dim]")
                else:
                    err_console.print(f"  [dim]Port {port} must be free for Meridian.[/dim]")
                err_console.print(f"  [dim]Stop {proc} and retry, or press Ctrl+C to abort.[/dim]")
                err_console.print()

            if yes:
                needed_for = " (Meridian's nginx needs it on 127.0.0.1)" if port in internal else ""
                fail(
                    f"Port {port} is occupied by {proc}{needed_for}",
                    hint=f"Stop {proc} or move it off port {port}, then retry.",
                    hint_type="user",
                )

            choice = choose("Retry?", ["Yes", "No"])
            if choice == 2:
                fail("Aborted -- port conflict", hint_type="user")
