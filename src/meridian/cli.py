"""Meridian CLI — main Typer application."""

from __future__ import annotations

import sys

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
client_app = typer.Typer(help="Manage proxy clients (add, remove, enable, disable)", no_args_is_help=True)
server_app = typer.Typer(help="Manage known servers", no_args_is_help=True)
node_app = typer.Typer(help="Manage proxy nodes in the fleet", no_args_is_help=True)
relay_app = typer.Typer(help="Manage relay nodes", no_args_is_help=True)
fleet_app = typer.Typer(help="Fleet-wide health, inventory, and recovery", no_args_is_help=True)
api_app = typer.Typer(help="Machine-readable Meridian API contracts", no_args_is_help=True)
dev_app = typer.Typer(help="Developer tools for testing and debugging", no_args_is_help=True)
app.add_typer(client_app, name="client")
app.add_typer(server_app, name="server")
app.add_typer(node_app, name="node")
app.add_typer(relay_app, name="relay")
app.add_typer(fleet_app, name="fleet")
app.add_typer(api_app, name="api")
app.add_typer(dev_app, name="dev", hidden=True)


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="Show version and exit"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logging"),
    json_mode: bool = typer.Option(False, "--json", help="JSON output to stdout (for scripting)"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress progress output"),
) -> None:
    """Meridian — Censorship-resistant proxy server management."""
    if ctx.resilient_parsing:
        return

    if verbose:
        import logging

        logging.basicConfig(
            level=logging.DEBUG,
            format="  %(name)s: %(message)s",
            handlers=[logging.StreamHandler(sys.stderr)],
        )

    machine_output = (
        json_mode
        or ctx.invoked_subcommand == "api"
        or _argv_requests_machine_output()
        or _argv_requests_machine_output(list(ctx.args))
    )
    if machine_output:
        from meridian.console import set_quiet_mode

        set_quiet_mode(True)

    if json_mode:
        from meridian.console import error_context, fail, set_json_mode

        set_json_mode(True)
        candidate_args = [list(ctx.args), sys.argv[1:]]
        if ctx.invoked_subcommand:
            candidate_args.append([ctx.invoked_subcommand])
        if not any(args and _argv_supports_global_json(args) for args in candidate_args):
            command = next((_argv_command_name(args) for args in candidate_args if args), "cli")
            with error_context(command):
                fail(
                    f"JSON output is not yet supported for `{command}`",
                    hint="Run `meridian api commands --json` to list supported machine-readable commands.",
                    hint_type="user",
                )

    if quiet:
        from meridian.console import set_quiet_mode

        set_quiet_mode(True)

    if version:
        print(f"meridian {__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        raise typer.Exit()

    # Show banner before subcommands (but not for --help)
    if not machine_output and "--help" not in sys.argv and "-h" not in sys.argv:
        banner(__version__)

    # Auto-update check (skip for meta commands)
    if not DISABLE_UPDATE_CHECK and not machine_output and ctx.invoked_subcommand not in ("update",):
        from meridian.update import check_for_update

        check_for_update(__version__)


def _enable_json_output() -> None:
    """Enable JSON output from command-local --json flags."""
    from meridian.console import set_json_mode, set_quiet_mode

    set_json_mode(True)
    set_quiet_mode(True)


def _argv_requests_machine_output(args: list[str] | None = None) -> bool:
    """Detect command-local machine-output flags before Typer enters subcommands."""
    args = args or sys.argv[1:]
    if "--json" in args or "--envelope" in args:
        return True
    if "--events" in args or any(arg.startswith("--events=") for arg in args):
        return True

    # `meridian api schema NAME` writes raw JSON Schema even without a flag.
    positionals = [arg for arg in args if not arg.startswith("-")]
    return len(positionals) >= 2 and positionals[:2] == ["api", "schema"]


def _argv_positionals(args: list[str] | None = None) -> list[str]:
    """Return argv tokens that identify the command path."""
    args = args or sys.argv[1:]
    positionals: list[str] = []
    skip_next = False
    value_options = {
        "--config",
        "--domain",
        "--server",
        "--sni",
        "--user",
        "--ssh-port",
        "--port",
        "--assets-dir",
        "--prune-extras",
        "--parallel",
        "--request",
        "--events",
    }
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in value_options:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        positionals.append(arg)
    return positionals


def _argv_command_name(args: list[str] | None = None) -> str:
    """Best-effort command name for early global JSON validation."""
    positionals = _argv_positionals(args)
    if not positionals:
        return "cli"
    if positionals[0] in {"client", "fleet", "api", "node", "relay"} and len(positionals) >= 2:
        return f"{positionals[0]}.{positionals[1]}"
    return positionals[0]


def _argv_supports_global_json(args: list[str] | None = None) -> bool:
    """Return True when global --json maps to a migrated command envelope."""
    positionals = _argv_positionals(args)
    if not positionals:
        return False
    if positionals[0] in {"plan", "apply"}:
        return True
    if positionals[0] == "deploy":
        return True
    if positionals[:2] in (
        ["client", "list"],
        ["client", "show"],
        ["client", "add"],
        ["client", "remove"],
        ["client", "enable"],
        ["client", "disable"],
        ["fleet", "status"],
        ["fleet", "inventory"],
        ["node", "list"],
        ["relay", "list"],
    ):
        return True
    return positionals[0] == "api"


# =============================================================================
# Studio
# =============================================================================


@app.command("studio")
def studio_cmd(
    port: int = typer.Option(0, "--port", help="Localhost port; 0 picks an available high port"),
    assets_dir: str = typer.Option("", "--assets-dir", help="Directory containing built Studio assets"),
    no_open: bool = typer.Option(False, "--no-open", help="Print the URL without opening a browser"),
) -> None:
    """Open Meridian Studio with the localhost Engine API"""
    from meridian.commands.studio import run

    run(port=port, assets_dir=assets_dir, no_open=no_open)


# =============================================================================
# Plan / Apply (declarative workflow)
# =============================================================================


@app.command("plan")
def plan_cmd(
    json_output: bool = typer.Option(False, "--json", help="Emit plan as JSON for CI consumption"),
) -> None:
    """Show what would change — compare desired state with actual.

    Reads desired_nodes, desired_clients, and desired_relays from cluster.yml
    and compares with the live panel state.

    [dim]Exit codes:[/dim]
      [green]0[/green] = already converged
      [yellow]2[/yellow] = changes pending
    """
    from meridian.commands.plan import run
    from meridian.console import is_json_mode

    if json_output:
        _enable_json_output()
    run(json_output=json_output or is_json_mode())


@app.command("apply")
def apply_cmd(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    prune_extras: str = typer.Option(
        "ask",
        "--prune-extras",
        help="How to handle extras (panel-side resources not in cluster.yml): "
        "ask (interactive prompt), yes (auto-remove), no (skip removal). "
        "Defaults to 'ask' interactively or 'no' under --yes for safety",
    ),
    parallel: int = typer.Option(
        4,
        "--parallel",
        help="Max parallel node provisioning threads",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit final apply result as JSON"),
) -> None:
    """Apply desired state — converge infrastructure to cluster.yml.

    Computes a plan, shows it, asks for confirmation, then executes.

    [dim]Examples:[/dim]
      [cyan]meridian apply[/cyan]              Show plan and confirm
      [cyan]meridian apply --yes[/cyan]        Apply without confirmation
      [cyan]meridian apply --prune-extras=yes[/cyan]   Auto-remove panel resources missing from cluster.yml
    """
    from meridian.commands.apply import run
    from meridian.console import is_json_mode

    if prune_extras not in ("ask", "yes", "no"):
        from meridian.console import fail

        fail(f"Invalid --prune-extras value: {prune_extras!r}", hint="Use ask, yes, or no", hint_type="user")

    json_enabled = json_output or is_json_mode()
    if json_enabled:
        _enable_json_output()
    run(yes=yes, parallel=parallel, prune_extras=prune_extras, json_output=json_enabled)


# =============================================================================
# Setup and legacy deploy
# =============================================================================


@app.command("setup")
def setup_cmd(
    intent_path: str = typer.Option(
        "",
        "--intent",
        help="Start at review from a complete SetupIntent JSON file, or '-' for stdin",
    ),
    restart: bool = typer.Option(
        False,
        "--restart",
        help="Explicitly discard saved setup progress before starting",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Approve review and apply without confirmation",
    ),
) -> None:
    """Configure the complete topology with a resumable guided wizard."""
    from meridian.commands.setup_v4 import run

    run(
        intent_path=intent_path,
        restart=restart,
        yes=yes,
    )


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
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    harden: bool = typer.Option(
        True,
        "--harden/--no-harden",
        help="Harden SSH and firewall (disable if other services share the server)",
    ),
    server: str = typer.Option("", "--server", help="Target server name or IP"),
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
    ssh_port: int = typer.Option(22, "--ssh-port", help="SSH port if non-standard"),
    json_output: bool = typer.Option(False, "--json", help="Emit final deploy result as JSON"),
    events: str = typer.Option("", "--events", help="Stream progress events as JSONL (use: jsonl)"),
    request_path: str = typer.Option("", "--request", help="Deploy-request JSON file or '-' for stdin"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate and plan without SSH changes"),
) -> None:
    """Deploy a VLESS+Reality proxy server

    Interactive wizard if no IP provided.

    [dim]Examples:[/dim]
      [cyan]meridian deploy[/cyan]                          Interactive wizard
      [cyan]meridian deploy 198.51.100.10[/cyan]            Deploy with defaults
      [cyan]meridian deploy 198.51.100.10 --domain vpn.example[/cyan]  CDN fallback via Cloudflare
      [cyan]meridian deploy 198.51.100.10 --no-harden[/cyan]  Skip SSH and firewall hardening
      [cyan]meridian deploy --request deploy.json --json --events=jsonl[/cyan]
    """
    if (
        not ip
        and not server
        and not request_path
        and not dry_run
        and not json_output
        and not events
        and not domain
        and not sni
        and not client_name
        and not display_name
        and not icon
        and not color
        and not warp
        and geo_block
        and harden
        and not yes
    ):
        from meridian.commands.setup_v4 import run as run_setup

        run_setup()
        return

    from meridian.commands.setup import run
    from meridian.console import is_json_mode

    json_enabled = json_output or is_json_mode() or bool(events)
    if json_enabled:
        _enable_json_output()
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
        warp=warp,
        geo_block=geo_block,
        ssh_port=ssh_port,
        request_path=request_path,
        json_output=json_enabled,
        events=events,
        dry_run=dry_run,
    )


# =============================================================================
# Client
# =============================================================================


@client_app.command("add")
def client_add(
    names: list[str] = typer.Argument(..., help="Client name(s) to add"),
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """Add one or more clients.

    [dim]Examples:[/dim]
      [cyan]meridian client add alice[/cyan]
      [cyan]meridian client add alice bob charlie[/cyan]
    """
    from meridian.commands.client import run_add

    if json_mode:
        _enable_json_output()
    run_add(names, json_mode=json_mode)


@client_app.command("show")
def client_show_cmd(
    name: str = typer.Argument(..., help="Client name"),
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """Show connection info for a client.

    [dim]Examples:[/dim]
      [cyan]meridian client show alice[/cyan]
    """
    from meridian.commands.client import run_show

    if json_mode:
        _enable_json_output()
    run_show(name)


@client_app.command("list")
def client_list_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """List all clients with status and traffic"""
    from meridian.commands.client import run_list

    if json_mode:
        _enable_json_output()
    run_list()


@client_app.command("remove")
def client_remove(
    name: str = typer.Argument(..., help="Client name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """Remove a client"""
    from meridian.commands.client import run_remove

    if json_mode:
        _enable_json_output()
    run_remove(name, yes=yes, json_mode=json_mode)


@client_app.command("enable")
def client_enable_cmd(
    name: str = typer.Argument(..., help="Client name"),
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """Resume a suspended client"""
    from meridian.commands.client import run_enable

    if json_mode:
        _enable_json_output()
    run_enable(name, json_mode=json_mode)


@client_app.command("disable")
def client_disable_cmd(
    name: str = typer.Argument(..., help="Client name"),
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """Temporarily suspend a client"""
    from meridian.commands.client import run_disable

    if json_mode:
        _enable_json_output()
    run_disable(name, json_mode=json_mode)


# =============================================================================
# Server
# =============================================================================


@server_app.command("add")
def server_add_cmd(
    ip: str = typer.Argument(..., help="Server IP address"),
    name: str = typer.Option("", "--name", help="Display name"),
    user: str = typer.Option("root", "--user", "-u", help="SSH user"),
    ssh_port: int = typer.Option(22, "--ssh-port", help="SSH port on the server"),
) -> None:
    """Add a known server"""
    from meridian.commands.server import run_add

    run_add(ip, name, user, ssh_port=ssh_port)


@server_app.command("list")
def server_list_cmd() -> None:
    """List known servers"""
    from meridian.commands.server import run_list

    run_list()


@server_app.command("remove")
def server_remove_cmd(
    server: str = typer.Argument(..., help="Server name or IP"),
) -> None:
    """Remove a known server"""
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
    server: str = typer.Option("", "--server", help="Target server name or IP"),
) -> None:
    """Validate server compatibility before deploying"""
    from meridian.commands.check import run

    run(ip, domain, sni, user, ai, server)


@app.command("scan")
def scan_cmd(
    ip: str = typer.Argument("", help="Server IP address"),
    user: str = typer.Option("", "--user", "-u", help="SSH user"),
    server: str = typer.Option("", "--server", help="Target server name or IP"),
) -> None:
    """Find optimal SNI targets via RealiTLScanner"""
    from meridian.commands.scan import run

    run(ip, user, server)


@app.command("test")
def test_cmd(
    ip: str = typer.Argument("", help="Server IP to test connectivity to"),
    domain: str = typer.Option("", "--domain", "-d", help="Domain to test"),
    sni: str = typer.Option("", "--sni", "-s", help="SNI target to test"),
    server: str = typer.Option("", "--server", help="Target server name or IP"),
) -> None:
    """Test proxy reachability from client device (no SSH required)"""
    from meridian.commands.ping import run

    run(ip, domain, sni, server)


@app.command("probe")
def probe_cmd(
    ip: str = typer.Argument("", help="Server IP or domain"),
    server: str = typer.Option("", "--server", help="Target server name or IP"),
) -> None:
    """Probe your server as a censor would"""
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
    server: str = typer.Option("", "--server", help="Target server name or IP"),
) -> None:
    """Collect system info for bug reports"""
    from meridian.commands.diagnostics import run

    run(ip, sni, user, ai, server)


# Alias: rage → doctor
@app.command("rage", hidden=True)
def rage_cmd(
    ip: str = typer.Argument("", help="Server IP address"),
    sni: str = typer.Option("", "--sni", "-s", help="SNI target"),
    user: str = typer.Option("", "--user", "-u", help="SSH user"),
    ai: bool = typer.Option(False, "--ai", help="Copy diagnostic prompt to clipboard"),
    server: str = typer.Option("", "--server", help="Target server name or IP"),
) -> None:
    """Alias for doctor"""
    from meridian.commands.diagnostics import run

    run(ip, sni, user, ai, server)


@app.command("teardown")
def teardown_cmd(
    ip: str = typer.Argument("", help="Server IP address"),
    user: str = typer.Option("", "--user", "-u", help="SSH user"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    server: str = typer.Option("", "--server", help="Target server name or IP"),
) -> None:
    """Remove proxy deployment from a server"""
    from meridian.commands.uninstall import run

    run(ip, user, yes, server)


@app.command("update")
def update_cmd() -> None:
    """Update meridian to the latest version"""
    from meridian.update import run_self_update

    run_self_update()


# =============================================================================
# Relay
# =============================================================================


@relay_app.command("deploy")
def relay_deploy_cmd(
    relay_ip: str = typer.Argument(..., help="Relay server IP address"),
    exit: str = typer.Option(..., "--exit", "-e", help="Exit server IP or name"),
    user: str = typer.Option("root", "--user", "-u", help="SSH user"),
    name: str = typer.Option("", "--name", help="Friendly name for the relay (e.g. ru-moscow)"),
    port: int = typer.Option(443, "--port", "-p", help="Relay listen port"),
    sni: str = typer.Option("", "--sni", help="Reality SNI target for relay (auto-scanned if omitted)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    ssh_port: int = typer.Option(22, "--ssh-port", help="SSH port on the relay server"),
) -> None:
    """Deploy a TCP relay that forwards to an exit node.

    [dim]Examples:[/dim]
      [cyan]meridian relay deploy 203.0.113.10 --exit 198.51.100.10[/cyan]
      [cyan]meridian relay deploy 203.0.113.10 --exit myserver --name ru-moscow[/cyan]
      [cyan]meridian relay deploy 203.0.113.10 --exit 198.51.100.10 --sni yandex.ru[/cyan]
    """
    from meridian.commands.relay import run_deploy

    run_deploy(relay_ip, exit, user, name, port, yes, sni=sni, ssh_port=ssh_port)


@relay_app.command("list")
def relay_list_cmd(
    exit: str = typer.Option("", "--exit", "-e", help="Filter by exit server IP or name"),
    user: str = typer.Option("", "--user", "-u", help="SSH user"),
) -> None:
    """List all relay nodes"""
    from meridian.commands.relay import run_list

    run_list(exit, user)


@relay_app.command("remove")
def relay_remove_cmd(
    relay_ip: str = typer.Argument(..., help="Relay IP to remove"),
    exit: str = typer.Option("", "--exit", "-e", help="Exit server IP or name"),
    user: str = typer.Option("", "--user", "-u", help="SSH user"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Remove a relay node"""
    from meridian.commands.relay import run_remove

    run_remove(relay_ip, exit, user, yes)


@relay_app.command("check")
def relay_check_cmd(
    relay_ip: str = typer.Argument(..., help="Relay IP to check"),
    exit: str = typer.Option("", "--exit", "-e", help="Exit server IP or name"),
    user: str = typer.Option("", "--user", "-u", help="SSH user"),
) -> None:
    """Run health checks on a relay"""
    from meridian.commands.relay import run_check

    run_check(relay_ip, exit, user)


# =============================================================================
# Node (multi-node fleet management)
# =============================================================================


@node_app.command("add")
def node_add_cmd(
    ip: str = typer.Argument(..., help="Server IP address"),
    name: str = typer.Option("", "--name", help="Friendly name for the node"),
    user: str = typer.Option("root", "--user", "-u", help="SSH user"),
    sni: str = typer.Option("", "--sni", help="Reality SNI target"),
    domain: str = typer.Option("", "--domain", help="Domain for WSS/XHTTP (Cloudflare CDN)"),
    ssh_port: int = typer.Option(22, "--ssh-port", help="SSH port"),
    harden: bool = typer.Option(True, help="Apply OS hardening (firewall, SSH)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Provision and add a new node to the fleet.

    Connects via SSH, hardens the server, installs Docker and Xray,
    registers with the panel, and creates host entries.

    [dim]Examples:[/dim]
      [cyan]meridian node add 198.51.100.10[/cyan]
      [cyan]meridian node add 198.51.100.10 --name finland --sni www.google.com[/cyan]
      [cyan]meridian node add 198.51.100.10 --domain proxy.example.com --yes[/cyan]
    """
    from meridian.commands.node import run_add

    run_add(ip, name=name, user=user, ssh_port=ssh_port, sni=sni, domain=domain, harden=harden, yes=yes)


@node_app.command("list")
def node_list_cmd() -> None:
    """List all nodes with live status"""
    from meridian.commands.node import run_list

    run_list()


@node_app.command("check")
def node_check_cmd(
    node: str = typer.Argument(..., help="Node IP or name"),
    user: str = typer.Option("", "--user", "-u", help="SSH user"),
) -> None:
    """Run health checks on a node"""
    from meridian.commands.node import run_check

    run_check(node, user=user)


@node_app.command("remove")
def node_remove_cmd(
    node: str = typer.Argument(..., help="Node IP or name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    force: bool = typer.Option(False, "--force", help="Remove even if relays depend on this node"),
) -> None:
    """Remove a node from the fleet"""
    from meridian.commands.node import run_remove

    run_remove(node, yes=yes, force=force)


# =============================================================================
# Fleet (health & recovery)
# =============================================================================


@fleet_app.command("status")
def fleet_status_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """Show fleet health — panel, nodes, relays, clients"""
    from meridian.commands.fleet import run_status

    if json_mode:
        _enable_json_output()
    run_status()


@fleet_app.command("inventory")
def fleet_inventory_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """Show configured topology with live status"""
    from meridian.commands.fleet import run_inventory

    if json_mode:
        _enable_json_output()
    run_inventory()


@fleet_app.command("recover")
def fleet_recover_cmd(
    panel_url: str = typer.Option(..., "--panel-url", help="Panel HTTPS URL"),
    api_token: str = typer.Option(..., "--api-token", help="Remnawave API token (JWT)"),
) -> None:
    """Reconstruct cluster.yml from the panel API.

    Use when ~/.meridian/ is lost but the panel is still running.

    [dim]Examples:[/dim]
      [cyan]meridian fleet recover --panel-url https://198.51.100.10/panel --api-token eyJ...[/cyan]
    """
    from meridian.commands.recover import run_recover

    run_recover(panel_url, api_token)


# =============================================================================
# API contracts
# =============================================================================


@api_app.command("schemas")
def api_schemas_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Output schema catalog as JSON envelope"),
    include_schemas: bool = typer.Option(False, "--include-schemas", help="Include full JSON schemas in output"),
) -> None:
    """List meridian-core JSON schemas"""
    from meridian.commands.api import run_schemas

    if json_mode:
        _enable_json_output()
    run_schemas(json_output=json_mode, include_schemas=include_schemas)


@api_app.command("commands")
def api_commands_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Output command catalog as JSON envelope"),
    include_schemas: bool = typer.Option(False, "--include-schemas", help="Include envelope and data schemas"),
) -> None:
    """List migrated meridian-core command contracts"""
    from meridian.commands.api import run_commands

    if json_mode:
        _enable_json_output()
    run_commands(json_output=json_mode, include_schemas=include_schemas)


@api_app.command("schema")
def api_schema_cmd(
    name: str = typer.Argument(..., help="Schema name from 'meridian api schemas'"),
    envelope_output: bool = typer.Option(False, "--envelope", help="Wrap schema in meridian.output/v1"),
    json_mode: bool = typer.Option(False, "--json", help="Alias for --envelope"),
) -> None:
    """Print one meridian-core JSON schema"""
    from meridian.commands.api import run_schema

    if envelope_output or json_mode:
        _enable_json_output()
    run_schema(name, envelope_output=envelope_output or json_mode)


@api_app.command("workflow")
def api_workflow_cmd(
    name: str = typer.Argument(..., help="Workflow name (e.g. deploy)"),
    json_mode: bool = typer.Option(False, "--json", help="Output workflow plan as JSON envelope"),
) -> None:
    """Print one meridian-core workflow plan"""
    from meridian.commands.api import run_workflow

    if json_mode:
        _enable_json_output()
    run_workflow(name, json_output=json_mode)


# =============================================================================
# Dev tools
# =============================================================================


@dev_app.command("preview")
def dev_preview_cmd(
    port: int = typer.Option(8787, "--port", "-p", help="Local server port"),
    name: str = typer.Option("demo", "--name", help="Client name for preview"),
    ip: str = typer.Option("198.51.100.1", "--ip", help="Demo server IP"),
    no_open: bool = typer.Option(False, "--no-open", help="Print URL without opening browser"),
    output: str = typer.Option("", "--output", "-o", help="Write files to directory instead of serving"),
    watch: bool = typer.Option(False, "--watch", "-w", help="Watch source files and live-reload on change"),
) -> None:
    """Preview PWA connection page locally (no VPS required)

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
