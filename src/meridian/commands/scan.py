"""SNI scanner — find optimal Reality SNI targets on the server's network."""

from __future__ import annotations

import typer

from meridian.commands._helpers import load_cluster
from meridian.commands.resolve import ensure_server_connection, resolve_server
from meridian.config import SERVER_PROFILES_FILE
from meridian.console import err_console, fail, info, line, ok, prompt, warn
from meridian.diagnostics.camouflage import ScanUnavailableError, scan_neighbors
from meridian.servers import ServerRegistry
from meridian.ssh import ServerConnection


def scan_for_sni(conn: ServerConnection, ip: str, *, strict: bool = False) -> list[str]:
    """Run RealiTLScanner on the server and return the discovered SNI targets.

    Returns an empty list for operational failures in optional mode and when a
    completed scan finds no candidates. Strict mode raises ``ScanUnavailableError``.
    Does NOT prompt the user -- just returns the candidates.
    """
    from rich.status import Status

    with Status("  [cyan]\u2192 Scanning the server's subnet...[/cyan]", console=err_console, spinner="dots"):
        try:
            return scan_neighbors(conn, ip).domains
        except ScanUnavailableError as exc:
            if strict:
                raise
            warn(str(exc))
            return []


def run(
    ip: str = "",
    user: str = "",
    *,
    ssh_port: int = 0,
) -> None:
    """Download RealiTLScanner, scan the server's subnet, and let user pick an SNI target."""
    registry = ServerRegistry(SERVER_PROFILES_FILE)
    resolved = resolve_server(registry, explicit_ip=ip, user=user, port=ssh_port)

    resolved = ensure_server_connection(resolved)

    err_console.print()
    err_console.print("  [bold]SNI Scanner[/bold]")
    err_console.print("  [dim]Finding optimal Reality SNI targets near your server...[/dim]")
    err_console.print()

    try:
        domains = scan_for_sni(resolved.conn, resolved.ip, strict=True)
    except ScanUnavailableError as exc:
        fail(
            str(exc),
            hint="Check the server's network access and retry, or provide --sni during setup.",
            hint_type="system",
        )

    if not domains:
        warn("No suitable SNI targets found.")
        err_console.print(f"\n  [dim]You can manually set SNI with: meridian deploy {resolved.ip} --sni DOMAIN[/dim]\n")
        raise typer.Exit(4)

    err_console.print()
    line()
    err_console.print()
    err_console.print(f"  [bold]Found {len(domains)} SNI targets:[/bold]\n")

    for i, domain in enumerate(domains, 1):
        err_console.print(f"    [info]{i}[/info]) {domain}")

    err_console.print()
    choice = prompt(f"Select a target (1-{len(domains)}) or press Enter to skip")

    if choice:
        if not choice.isdigit():
            warn(f"Enter a number from 1 to {len(domains)}, or press Enter to skip")
            raise typer.Exit(2)
        idx = int(choice)
        if 1 <= idx <= len(domains):
            selected = domains[idx - 1]

            # A saved topology is a reviewed contract: it only changes through setup/apply.
            cluster = load_cluster()
            if cluster.topology_intent is not None:
                warn("Your topology is review-managed; the selected SNI was not saved")
            err_console.print()
            ok(f"Selected: {selected}")
            if cluster.topology_intent is not None:
                err_console.print(f"  [dim]Switch this exit to it: meridian node sni {resolved.ip} {selected}[/dim]")
                err_console.print("  [dim]Or update the reviewed Reality SNI through: meridian setup[/dim]")
            else:
                err_console.print(f"  [dim]Use it: meridian deploy {resolved.ip} --sni {selected}[/dim]")
            err_console.print(
                "  [dim]Tip: a domain of your own pointed at the server works too: add --self-steal.[/dim]\n"
            )
            return

        warn(f"Enter a number from 1 to {len(domains)}, or press Enter to skip")
        raise typer.Exit(2)

    info("No target selected. Deploy will pick a working default camouflage target.")
    err_console.print()
