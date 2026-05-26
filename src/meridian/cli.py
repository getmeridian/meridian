"""Meridian CLI — main Typer application."""

from __future__ import annotations

import typer

from meridian import __version__
from meridian.config import DISABLE_UPDATE_CHECK
from meridian.console import banner

app = typer.Typer(
    name="meridian",
    help="Censorship-resistant proxy server management",
    epilog="[dim]Docs: https://getmeridian.org/docs[/dim]",
    add_completion=True,
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# Subcommand groups
client_app = typer.Typer(help="Manage proxy clients", no_args_is_help=True)
server_app = typer.Typer(help="Manage known servers", no_args_is_help=True)
relay_app = typer.Typer(help="Manage relay nodes", no_args_is_help=True)
dev_app = typer.Typer(help="Developer tools for testing and debugging", no_args_is_help=True)
app.add_typer(client_app, name="client")
app.add_typer(server_app, name="server")
app.add_typer(relay_app, name="relay")
app.add_typer(dev_app, name="dev", hidden=True)


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="Show version and exit"),
) -> None:
    """Meridian — Censorship-resistant proxy server management."""
    if ctx.resilient_parsing:
        return

    if version:
        print(f"meridian {__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        raise typer.Exit()

    # Show banner before subcommands (but not for --help)
    import sys

    if "--help" not in sys.argv and "-h" not in sys.argv:
        banner(__version__)

    # Auto-update check (skip for meta commands)
    if not DISABLE_UPDATE_CHECK and ctx.invoked_subcommand not in ("update",):
        from meridian.update import check_for_update

        check_for_update(__version__)


# =============================================================================
# Deploy
# =============================================================================


@app.command("deploy")
def deploy_cmd(
    ip: str = typer.Argument("", help="Server IP address (or 'local' to deploy on this server)"),
    domain: str = typer.Option(
        "",
        "--domain",
        "-d",
        help="Cloudflare CDN fallback domain",
    ),
    sni: str = typer.Option(
        "",
        "--sni",
        "-s",
        help="TLS camouflage target (use 'meridian scan' to find best)",
    ),
    client_name: str = typer.Option("", "--client-name", help="Name for the first client"),
    user: str = typer.Option("root", "--user", "-u", help="SSH user"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip all prompts (use defaults)"),
    harden: bool = typer.Option(
        True,
        "--harden/--no-harden",
        help="Harden SSH + firewall (disable if other services share the server)",
    ),
    server: str = typer.Option("", "--server", help="Target server (name or IP)"),
    display_name: str = typer.Option(
        "",
        "--display-name",
        help="Label for connection pages (e.g. 'Alice\\'s VPN')",
        rich_help_panel="Branding",
    ),
    icon: str = typer.Option("", "--icon", help="Page icon (emoji or image URL)", rich_help_panel="Branding"),
    color: str = typer.Option(
        "", "--color", help="Page color theme (ocean/sunset/forest/lavender/rose/slate)", rich_help_panel="Branding"
    ),
    decoy: str = typer.Option("", "--decoy", hidden=True, help="Deprecated: 403/404 is now always used"),
    pq: bool = typer.Option(
        False,
        "--pq/--no-pq",
        help="Post-quantum encryption — ML-KEM-768 hybrid (experimental)",
    ),
    warp: bool = typer.Option(
        False,
        "--warp/--no-warp",
        help="Route outgoing traffic through Cloudflare WARP",
    ),
    geo_block: bool = typer.Option(
        True,
        "--geo-block/--no-geo-block",
        help="Block Russian domains and IPs (geosite:category-ru + geoip:ru)",
    ),
    ssh_port: int = typer.Option(22, "--ssh-port", help="SSH port (if non-standard)"),
) -> None:
    """Deploy a VLESS+Reality proxy server. Interactive wizard if no IP provided.

    [dim]Examples:[/dim]
      [cyan]meridian deploy[/cyan]                          Interactive wizard
      [cyan]meridian deploy 1.2.3.4[/cyan]                  Deploy with defaults
      [cyan]meridian deploy 1.2.3.4 --domain d.io[/cyan]    CDN fallback via Cloudflare
      [cyan]meridian deploy 1.2.3.4 --no-harden[/cyan]      Skip SSH + firewall hardening
    """
    from meridian.commands.setup import run

    run(
        ip,
        domain,
        sni,
        client_name,
        user,
        yes,
        harden,
        server,
        server_name=display_name,
        icon=icon,
        color=color,
        decoy=decoy,
        pq=pq,
        warp=warp,
        geo_block=geo_block,
        ssh_port=ssh_port,
    )


# =============================================================================
# Client
# =============================================================================


@client_app.command("add")
def client_add(
    name: str = typer.Argument(..., help="Client name"),
    server: str = typer.Option("", "--server", help="Target server (name or IP)"),
    user: str = typer.Option("", "--user", "-u", help="SSH user"),
) -> None:
    """Add a new client.

    [dim]Examples:[/dim]
      [cyan]meridian client add alice[/cyan]
      [cyan]meridian client add alice --server myserver[/cyan]
    """
    from meridian.commands.client import run_add

    run_add(name, user, server)


@client_app.command("show")
def client_show_cmd(
    name: str = typer.Argument(..., help="Client name"),
    server: str = typer.Option("", "--server", help="Target server (name or IP)"),
    user: str = typer.Option("", "--user", "-u", help="SSH user"),
) -> None:
    """Show connection info for an existing client.

    [dim]Examples:[/dim]
      [cyan]meridian client show alice[/cyan]
      [cyan]meridian client show alice --server myserver[/cyan]
    """
    from meridian.commands.client import run_show

    run_show(name, user, server)


@client_app.command("list")
def client_list_cmd(
    server: str = typer.Option("", "--server", help="Target server (name or IP)"),
    user: str = typer.Option("", "--user", "-u", help="SSH user"),
) -> None:
    """List all clients."""
    from meridian.commands.client import run_list

    run_list(user, server)


@client_app.command("remove")
def client_remove(
    name: str = typer.Argument(..., help="Client name"),
    server: str = typer.Option("", "--server", help="Target server (name or IP)"),
    user: str = typer.Option("", "--user", "-u", help="SSH user"),
) -> None:
    """Remove a client."""
    from meridian.commands.client import run_remove

    run_remove(name, user, server)


# =============================================================================
# Server
# =============================================================================


@server_app.command("add")
def server_add_cmd(
    ip: str = typer.Argument(..., help="Server IP address"),
    name: str = typer.Option("", "--name", help="Display name"),
    user: str = typer.Option("root", "--user", "-u", help="SSH user"),
) -> None:
    """Add a known server."""
    from meridian.commands.server import run_add

    run_add(ip, name, user)


@server_app.command("list")
def server_list_cmd() -> None:
    """List known servers."""
    from meridian.commands.server import run_list

    run_list()


@server_app.command("remove")
def server_remove_cmd(
    server: str = typer.Argument(..., help="Server name or IP"),
) -> None:
    """Remove a known server."""
    from meridian.commands.server import run_remove

    run_remove(server)


# =============================================================================
# Preflight / Scan / Test
# =============================================================================


@app.command("preflight")
def preflight_cmd(
    ip: str = typer.Argument("", help="Server IP address"),
    domain: str = typer.Option("", "--domain", "-d", help="Domain to check"),
    sni: str = typer.Option("", "--sni", "-s", help="SNI target to verify"),
    user: str = typer.Option("", "--user", "-u", help="SSH user"),
    ai: bool = typer.Option(False, "--ai", help="Copy diagnostic prompt to clipboard"),
    server: str = typer.Option("", "--server", help="Target server (name or IP)"),
) -> None:
    """Validate server compatibility (SNI, ports, DNS, OS, disk, ASN) before deploying."""
    from meridian.commands.check import run

    run(ip, domain, sni, user, ai, server)


@app.command("scan")
def scan_cmd(
    ip: str = typer.Argument("", help="Server IP address"),
    user: str = typer.Option("", "--user", "-u", help="SSH user"),
    server: str = typer.Option("", "--server", help="Target server (name or IP)"),
) -> None:
    """Find optimal SNI targets via RealiTLScanner."""
    from meridian.commands.scan import run

    run(ip, user, server)


@app.command("test")
def test_cmd(
    ip: str = typer.Argument("", help="Server IP to test connectivity to"),
    domain: str = typer.Option("", "--domain", "-d", help="Domain to test"),
    sni: str = typer.Option("", "--sni", "-s", help="SNI target to test"),
    server: str = typer.Option("", "--server", help="Target server (name or IP)"),
) -> None:
    """Test proxy reachability from client device (no SSH required)."""
    from meridian.commands.ping import run

    run(ip, domain, sni, server)


@app.command("probe")
def probe_cmd(
    ip: str = typer.Argument("", help="Server IP or domain"),
    server: str = typer.Option("", "--server", help="Target server (name or IP)"),
) -> None:
    """Probe your server as a censor would — check if the deployment is detectable."""
    from meridian.commands.probe import run

    run(ip, server)


# =============================================================================
# Doctor / Teardown / Update
# =============================================================================


@app.command("doctor")
def doctor_cmd(
    ip: str = typer.Argument("", help="Server IP address"),
    sni: str = typer.Option("", "--sni", "-s", help="SNI target"),
    user: str = typer.Option("", "--user", "-u", help="SSH user"),
    ai: bool = typer.Option(False, "--ai", help="Copy diagnostic prompt to clipboard"),
    server: str = typer.Option("", "--server", help="Target server (name or IP)"),
) -> None:
    """Collect system info for bug reports. Use --ai for ChatGPT/Claude prompt."""
    from meridian.commands.diagnostics import run

    run(ip, sni, user, ai, server)


# Alias: rage → doctor
@app.command("rage", hidden=True)
def rage_cmd(
    ip: str = typer.Argument("", help="Server IP address"),
    sni: str = typer.Option("", "--sni", "-s", help="SNI target"),
    user: str = typer.Option("", "--user", "-u", help="SSH user"),
    ai: bool = typer.Option(False, "--ai", help="Copy diagnostic prompt to clipboard"),
    server: str = typer.Option("", "--server", help="Target server (name or IP)"),
) -> None:
    """Alias for doctor."""
    from meridian.commands.diagnostics import run

    run(ip, sni, user, ai, server)


@app.command("teardown")
def teardown_cmd(
    ip: str = typer.Argument("", help="Server IP address"),
    user: str = typer.Option("", "--user", "-u", help="SSH user"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    server: str = typer.Option("", "--server", help="Target server (name or IP)"),
) -> None:
    """Remove proxy deployment from server."""
    from meridian.commands.uninstall import run

    run(ip, user, yes, server)


@app.command("update")
def update_cmd() -> None:
    """Update meridian to the latest version."""
    from meridian.update import run_self_update

    run_self_update()


# =============================================================================
# Relay
# =============================================================================


@relay_app.command("deploy")
def relay_deploy_cmd(
    relay_ip: str = typer.Argument(..., help="Relay server IP address"),
    exit: str = typer.Option(..., "--exit", "-e", help="Exit server (IP or name)"),
    user: str = typer.Option("", "--user", "-u", help="SSH user for the relay (defaults to registry entry or 'root')"),
    exit_user: str = typer.Option(
        "", "--exit-user", help="SSH user for the exit (defaults to registry entry or 'root')"
    ),
    name: str = typer.Option("", "--name", help="Friendly name for the relay (e.g., ru-moscow)"),
    port: int = typer.Option(443, "--port", "-p", help="Relay listen port"),
    sni: str = typer.Option("", "--sni", help="Reality SNI target for relay (auto-scanned if omitted)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    ssh_port: int = typer.Option(22, "--ssh-port", help="SSH port on the relay server"),
) -> None:
    """Deploy a TCP relay that forwards to an exit server.

    [dim]Examples:[/dim]
      [cyan]meridian relay deploy 1.2.3.4 --exit 5.6.7.8[/cyan]
      [cyan]meridian relay deploy 1.2.3.4 --exit myserver --name ru-moscow[/cyan]
      [cyan]meridian relay deploy 1.2.3.4 --exit 5.6.7.8 --sni yandex.ru[/cyan]
      [cyan]meridian relay deploy 1.2.3.4 -u alice --exit 5.6.7.8 --exit-user bob[/cyan]
    """
    from meridian.commands.relay import run_deploy

    run_deploy(relay_ip, exit, user, name, port, yes, sni=sni, ssh_port=ssh_port, exit_user=exit_user)


@relay_app.command("list")
def relay_list_cmd(
    exit: str = typer.Option("", "--exit", "-e", help="Filter by exit server (IP or name)"),
    user: str = typer.Option("", "--user", "-u", help="SSH user for the relay (defaults to registry entry or 'root')"),
    exit_user: str = typer.Option(
        "", "--exit-user", help="SSH user for the exit (defaults to registry entry or 'root')"
    ),
) -> None:
    """List relay nodes."""
    from meridian.commands.relay import run_list

    run_list(exit, user, exit_user=exit_user)


@relay_app.command("remove")
def relay_remove_cmd(
    relay_ip: str = typer.Argument(..., help="Relay IP to remove"),
    exit: str = typer.Option("", "--exit", "-e", help="Exit server (IP or name)"),
    user: str = typer.Option("", "--user", "-u", help="SSH user for the relay (defaults to registry entry or 'root')"),
    exit_user: str = typer.Option(
        "", "--exit-user", help="SSH user for the exit (defaults to registry entry or 'root')"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Remove a relay node."""
    from meridian.commands.relay import run_remove

    run_remove(relay_ip, exit, user, yes, exit_user=exit_user)


@relay_app.command("check")
def relay_check_cmd(
    relay_ip: str = typer.Argument(..., help="Relay IP to check"),
    exit: str = typer.Option("", "--exit", "-e", help="Exit server (IP or name)"),
    user: str = typer.Option("", "--user", "-u", help="SSH user for the relay (defaults to registry entry or 'root')"),
    exit_user: str = typer.Option(
        "", "--exit-user", help="SSH user for the exit (defaults to registry entry or 'root')"
    ),
) -> None:
    """Check health of a relay node."""
    from meridian.commands.relay import run_check

    run_check(relay_ip, exit, user, exit_user=exit_user)


# =============================================================================
# Dev tools
# =============================================================================


@dev_app.command("preview")
def dev_preview_cmd(
    port: int = typer.Option(8787, "--port", "-p", help="Local server port"),
    name: str = typer.Option("demo", "--name", help="Client name for preview"),
    ip: str = typer.Option("198.51.100.1", "--ip", help="Demo server IP"),
    no_open: bool = typer.Option(False, "--no-open", help="Don't open browser automatically"),
    output: str = typer.Option("", "--output", "-o", help="Write files to directory instead of serving"),
    watch: bool = typer.Option(False, "--watch", "-w", help="Watch source files and live-reload on change"),
) -> None:
    """Preview PWA connection page locally (no VPS required).

    Generates a complete connection page with demo data and serves it
    on localhost. All PWA features work: service worker, install prompt,
    offline mode, platform detection.

    [dim]Examples:[/dim]
      [cyan]meridian dev preview[/cyan]                   Launch preview in browser
      [cyan]meridian dev preview --watch[/cyan]            Live-reload on source changes
      [cyan]meridian dev preview --port 9000[/cyan]       Use custom port
      [cyan]meridian dev preview --name alice[/cyan]      Preview with client name
      [cyan]meridian dev preview -o /tmp/pwa[/cyan]       Save files without serving
    """
    from meridian.commands.dev import run_preview

    run_preview(port=port, client_name=name, server_ip=ip, no_open=no_open, output=output, watch=watch)
