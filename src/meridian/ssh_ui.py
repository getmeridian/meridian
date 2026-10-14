"""Rich-based SSHUI implementation for CLI callers.

This module depends on Rich/console and should only be imported from
CLI command modules — never from ``ssh.py`` itself.
"""

from __future__ import annotations

from meridian.console import err_console, info, ok, warn
from meridian.ssh import SSHError


class RichSSHUI:
    """SSHUI implementation that uses Rich console output.

    Drop-in replacement that reproduces the original coloured terminal
    output that ``ssh.py`` used before the decoupling.
    """

    def info(self, msg: str) -> None:
        info(msg)

    def ok(self, msg: str) -> None:
        ok(msg)

    def warn(self, msg: str) -> None:
        warn(msg)

    def host_key_prompt(self, ip: str, fingerprint: str, algo: str) -> bool:
        """Display host-key fingerprint and ask the user to accept."""
        err_console.print()
        err_console.print(f"  [warn]![/warn] First connection to {ip}")
        if fingerprint:
            err_console.print("  [dim]Host key fingerprint:[/dim]")
            err_console.print(f"  [bold]{fingerprint}[/bold]")
        else:
            err_console.print(f"  [dim]Host key type: {algo}[/dim]")
        err_console.print()
        err_console.print("  [dim]Verify this matches your VPS provider's console.[/dim]")
        err_console.print("  [dim]A mismatch may indicate a network attack.[/dim]")

        try:
            with open("/dev/tty") as tty:
                err_console.print("\n  [info]→[/info] Trust this host key? [dim][Y/n][/dim] ", end="")
                answer = tty.readline().strip().lower()
        except OSError:
            raise SSHError(
                f"Cannot verify host key for {ip} (no terminal available)",
                hint="Run interactively, or pre-add the key: ssh-keyscan IP >> ~/.ssh/known_hosts",
                category="user",
            )
        return answer in ("", "y", "yes")

    def ssh_failed(self, ip: str, user: str, stderr: str) -> None:
        err_console.print(f"\n  [error]SSH connection failed:[/error] {stderr}")
        err_console.print(f"  [dim]1. Copy your SSH key:  ssh-copy-id {user}@{ip}[/dim]")
        err_console.print(f"  [dim]2. Test manually:      ssh {user}@{ip}[/dim]")
        err_console.print("  [dim]3. Different user:     meridian deploy IP --user ubuntu[/dim]")

    def host_key_changed(self, ip: str) -> None:
        err_console.print(f"\n  [error]Host key for {ip} has CHANGED![/error]")
        err_console.print("  [warn]This could indicate a network attack (MitM).[/warn]")
        err_console.print("  [dim]If you recently rebuilt this server, remove the old key:[/dim]")
        err_console.print(f"  [dim]  ssh-keygen -R {ip}[/dim]")
