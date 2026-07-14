"""Relay management -- deploy, list, remove, check relay nodes.

Relays are lightweight Realm TCP forwarders that provide domestic entry
points. All relay-to-panel mapping uses Remnawave Host entries; topology
is persisted in cluster.yml.
"""

from __future__ import annotations

import re
import shlex

import typer

from meridian.cluster import ClusterConfig, RelayEntry
from meridian.commands._helpers import load_cluster, make_panel
from meridian.commands._validation import validate_command_input
from meridian.config import RELAY_SERVICE_NAME
from meridian.console import confirm, err_console, fail, info, line, ok, warn
from meridian.core.command_inputs import RelayDeployRequest, RelayTargetRequest
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.relay_ops import (
    create_relay_hosts,
    delete_relay_hosts,
    deploy_relay_nginx,
    find_exit_node,
    remove_relay_nginx,
)
from meridian.remnawave import RemnawaveError
from meridian.ssh import ServerConnection, SSHError
from meridian.ssh_ui import RichSSHUI


def run_deploy(
    relay_ip: str,
    exit_arg: str,
    user: str = "root",
    relay_name: str = "",
    listen_port: int = 443,
    yes: bool = False,
    sni: str = "",
    ssh_port: int = 22,
) -> None:
    """Deploy a relay node that forwards traffic to an exit server."""
    request = validate_command_input(
        RelayDeployRequest,
        "Invalid relay deploy request",
        relay_ip=relay_ip,
        exit_arg=exit_arg,
        user=user,
        relay_name=relay_name,
        listen_port=listen_port,
        yes=yes,
        sni=sni,
        ssh_port=ssh_port,
    )

    cluster = load_cluster()
    if cluster.topology_intent is not None:
        _run_deploy_v4(request, cluster)
        return
    try:
        exit_ip = find_exit_node(cluster, request.exit_arg)
    except ValueError as exc:
        fail(str(exc), hint="List nodes: meridian node list", hint_type="user")
    exit_node = cluster.find_node(exit_ip)
    if exit_node is None:
        fail(f"Exit node {exit_ip} not found in cluster", hint_type="bug")
    if cluster.find_relay(request.relay_ip) is not None:
        fail(
            f"Relay {request.relay_ip} is already in the cluster",
            hint=f"To re-deploy, remove first: meridian relay remove {request.relay_ip}",
            hint_type="user",
        )

    # Explain relay concept
    err_console.print()
    err_console.print("  [bold]What is a relay?[/bold]")
    err_console.print("  [dim]A lightweight domestic server that forwards encrypted traffic abroad.[/dim]")
    err_console.print("  [dim]Runs Realm (TCP forwarder). Encryption is end-to-end.[/dim]")
    err_console.print()

    # Same-server warning
    if request.relay_ip == exit_ip:
        if request.listen_port == 443:
            fail(
                "Relay and exit are the same server -- port 443 is already in use",
                hint="Try: --port 8443",
                hint_type="user",
            )
        warn(f"Relay and exit are the same ({request.relay_ip}). Fine for testing, not for production.")

    ok(f"Exit node verified: {exit_ip}")

    # Connect to relay
    info(f"Connecting to relay server: {request.relay_ip}")
    relay_conn = ServerConnection(ip=request.relay_ip, user=request.user, port=request.ssh_port)
    try:
        relay_conn.check_ssh(ui=RichSSHUI())
    except SSHError as exc:
        fail(str(exc), hint=exc.hint, hint_type=exc.category)

    ok("SSH OK")

    # Check if relay port is already in use
    port_check = relay_conn.run(f"ss -tlnp sport = :{request.listen_port} 2>/dev/null", timeout=10)
    if port_check.returncode == 0 and f":{request.listen_port}" in port_check.stdout:
        # Extract process name
        process_name = process_info = ""
        for ss_line in port_check.stdout.strip().splitlines()[1:]:
            if f":{request.listen_port}" in ss_line and "users:" in ss_line:
                process_info = ss_line.split("users:")[1].strip().strip("()")
                m = re.search(r'"([^"]*)"', process_info)
                process_name = m.group(1) if m else ""
                break
        if process_name == "realm":
            warn(f"Previous relay service found on port {request.listen_port} -- stopping it")
            relay_conn.run(f"systemctl stop {RELAY_SERVICE_NAME} 2>/dev/null", timeout=15)
            relay_conn.run(f"systemctl disable {RELAY_SERVICE_NAME} 2>/dev/null", timeout=10)
            ok("Previous relay service stopped")
        else:
            msg = f"Port {request.listen_port} is already in use"
            if process_info:
                msg += f" by {process_info}"
            fail(msg, hint="Try: --port <OTHER_PORT>", hint_type="system")

    # Test relay -> exit connectivity
    tcp_test = relay_conn.run(f"nc -z -w 5 {shlex.quote(exit_ip)} 443 2>/dev/null", timeout=10)
    if tcp_test.returncode != 0:
        warn(f"Relay cannot reach exit {exit_ip}:443 -- will attempt deployment anyway")
    else:
        ok("Relay -> exit connectivity confirmed")

    # Determine relay SNI target
    relay_sni = request.sni
    if not relay_sni:
        from rich.status import Status

        from meridian.commands.scan import scan_for_sni

        err_console.print()
        err_console.print("  [bold]SNI Scanner[/bold]")
        err_console.print("  [dim]Finding optimal Reality SNI target near the relay server...[/dim]")

        with Status("  [cyan]-> Scanning relay subnet...[/cyan]", console=err_console, spinner="dots"):
            candidates = scan_for_sni(relay_conn, request.relay_ip)

        if candidates:
            from meridian.console import choose

            display = candidates[:8]
            choices = [f"{d}" for d in display] + ["Abort (rerun with --sni)"]
            choice = choose("Select camouflage target for relay", choices, default=1)
            if choice <= len(display):
                relay_sni = display[choice - 1]
                ok(f"Relay SNI target: {relay_sni}")
            else:
                fail("Aborted", hint="Pass --sni explicitly.", hint_type="user")
        else:
            fail("Could not find a relay-local SNI target", hint="Pass --sni explicitly.", hint_type="system")
        err_console.print()

    # Deployment summary
    from rich.panel import Panel

    from meridian.config import REALM_VERSION

    summary = (
        f"Relay:  {request.user}@{request.relay_ip}:{request.listen_port}  |  Exit: {exit_ip}:443\n"
        f"Engine: Realm v{REALM_VERSION}  |  Name: {request.relay_name or '(auto)'}  |  SNI: {relay_sni}\n\n"
        f"  Client -> {request.relay_ip}:{request.listen_port} -> {exit_ip}:443 -> Internet\n"
        f"  Encryption: end-to-end (relay cannot read content)"
    )
    err_console.print()
    err_console.print(Panel(summary, title="[bold]Relay deployment plan[/bold]", border_style="cyan", padding=(0, 2)))
    err_console.print()

    if not request.yes:
        if not confirm(f"Deploy relay to {request.user}@{request.relay_ip}?"):
            raise typer.Exit(1)

    # Run relay provisioner (Realm install -- panel-agnostic)
    from meridian.provision.progress import RichStepRenderer
    from meridian.provision.relay import RelayContext, build_relay_steps
    from meridian.provision.steps import Provisioner

    ctx = RelayContext(
        relay_ip=request.relay_ip,
        exit_ip=exit_ip,
        exit_port=443,
        listen_port=request.listen_port,
        user=request.user,
    )
    info(f"Configuring relay at {request.relay_ip}...")
    err_console.print()

    results = Provisioner(build_relay_steps(ctx)).run(relay_conn, ctx, renderer=RichStepRenderer())
    failed = [r for r in results if r.status == "failed"]
    if failed:
        fail("Relay deployment failed", hint=f"Step '{failed[0].name}' failed: {failed[0].detail}", hint_type="system")

    err_console.print()
    ok("Realm relay deployed successfully")

    # Create Remnawave Host entries for this relay
    panel = make_panel(cluster)
    with panel:
        info("Creating relay host entries in panel...")
        host_uuids = create_relay_hosts(
            panel,
            cluster,
            request.relay_ip,
            request.listen_port,
            relay_sni,
            request.relay_name,
        )

        # Enforce safest-first ordering after adding relay hosts
        from meridian.node_deploy import enforce_host_ordering

        enforce_host_ordering(panel)
    if not host_uuids:
        fail(
            "No host entries created -- check that inbounds are configured",
            hint="Run: meridian fleet status",
            hint_type="system",
        )

    # Deploy nginx relay map on exit server (for per-relay SNI routing)
    if relay_sni and relay_sni != (exit_node.sni or ""):
        info("Configuring nginx SNI routing on exit server...")
        exit_conn = ServerConnection(ip=exit_ip, user=exit_node.ssh_user, port=exit_node.ssh_port)
        try:
            exit_conn.check_ssh(ui=RichSSHUI())
        except SSHError as exc:
            fail(f"Cannot SSH to exit node {exit_ip}: {exc}", hint="Check exit node SSH access", hint_type="system")
        if not deploy_relay_nginx(exit_conn, relay_sni, request.relay_ip, request.relay_name):
            fail(
                "Relay nginx routing update failed on the exit server",
                hint="Fix nginx on the exit and retry.",
                hint_type="system",
            )

    # Save RelayEntry to cluster.yml
    relay_entry = RelayEntry(
        ip=request.relay_ip,
        name=request.relay_name,
        port=request.listen_port,
        exit_node_ip=exit_ip,
        host_uuids=host_uuids,
        sni=relay_sni,
        ssh_user=request.user,
        ssh_port=request.ssh_port,
    )
    cluster.backup()
    cluster.relays.append(relay_entry)
    cluster.save()

    # Hybrid sync — mirror the relay into desired_relays when the user manages
    # relays declaratively. Use the exit node's name when available so the
    # desired entry stays human-readable; fall back to the IP otherwise.
    from meridian.reconciler.snapshots import hybrid_sync_desired_relays_add

    exit_node_for_sync = cluster.find_node(exit_ip)
    exit_ref = exit_node_for_sync.name if exit_node_for_sync and exit_node_for_sync.name else exit_ip
    hybrid_sync_desired_relays_add(cluster, relay_entry, exit_node_ref=exit_ref)

    ok("Relay saved to cluster")

    # Success output
    err_console.print()
    ok(f"Relay {request.relay_ip} forwarding to exit {exit_ip}")
    relay_route = f"Client -> {request.relay_ip}:{request.listen_port} (domestic) -> {exit_ip}:443 (abroad) -> Internet"
    err_console.print(f"  [dim]{relay_route}[/dim]")
    err_console.print("  [dim]Subscriptions auto-update -- clients get relay URLs on next sync.[/dim]")
    err_console.print()
    err_console.print("  [bold]Next steps:[/bold]")
    err_console.print("    meridian client add alice          [dim]# relay URLs included[/dim]")
    err_console.print(f"    meridian relay check {request.relay_ip}    [dim]# verify relay health[/dim]")
    err_console.print("    meridian relay list                [dim]# list all relays[/dim]")
    err_console.print()
    line()


def _run_deploy_v4(
    request: RelayDeployRequest,
    cluster: ClusterConfig,
) -> None:
    """Translate imperative relay deploy into reviewed V4 topology intent."""
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.servers import ServerEntry, ServerRegistry
    from meridian.setup.editor import add_relay_to_intent
    from meridian.setup.runtime import SetupRuntime

    intent = cluster.topology_intent
    if intent is None:
        raise RuntimeError("V4 topology intent is missing")
    registry = ServerRegistry(SERVER_PROFILES_FILE)
    exit_ref = ""
    for exit_ in intent.exits:
        entry = registry.find(exit_.server_ref)
        selectors = {
            exit_.id,
            exit_.server_ref,
            *({entry.host, entry.name} if entry is not None else set()),
        }
        if request.exit_arg in selectors:
            exit_ref = exit_.id
            break
    if not exit_ref:
        fail(
            f"Exit {request.exit_arg!r} was not found in V4 topology.",
            hint="Run `meridian setup` to review exit roles.",
            hint_type="user",
        )
    if not request.yes and not confirm(f"Add {request.relay_name or request.relay_ip} as a relay to {exit_ref}?"):
        raise typer.Exit(1)

    connection = ServerConnection(
        ip=request.relay_ip,
        user=request.user,
        port=request.ssh_port,
    )
    try:
        connection.check_ssh(ui=RichSSHUI())
    except SSHError as exc:
        fail(str(exc), hint=exc.hint, hint_type=exc.category)
    registry.add(
        ServerEntry(
            host=request.relay_ip,
            user=request.user,
            name=request.relay_name or request.relay_ip,
            port=request.ssh_port,
            auth_state="validated",
        )
    )
    entry = registry.find(request.relay_ip)
    if entry is None:
        raise RuntimeError("Validated relay server was not saved")
    updated = add_relay_to_intent(
        intent,
        server_ref=entry.id,
        title=(request.relay_name or f"relay-{len(intent.transparent_relays) + 1}"),
        exit_ref=exit_ref,
        listen_port=request.listen_port,
        reality_sni=request.sni,
    )
    result = SetupRuntime(
        registry,
        cluster_loader=lambda: cluster,
    ).apply_intent(updated)
    if not result.all_succeeded:
        failures = "; ".join(f"{item.action.resource.logical_id}: {item.error}" for item in result.failed)
        fail(
            "V4 relay did not converge.",
            hint=failures,
            hint_type="system",
        )
    ok(f"Relay {request.relay_name or request.relay_ip} {'added' if result.changed else 'already converged'}")


def run_list(
    exit_arg: str = "",
    user: str = "",
) -> None:
    """List relay nodes from cluster configuration."""
    from meridian.console import error_context

    operation = OperationContext()
    with error_context("relay.list", timer=operation.timer):
        _run_list(exit_arg=exit_arg, user=user, operation=operation)


def _run_list(
    *,
    exit_arg: str = "",
    user: str = "",
    operation: OperationContext,
) -> None:
    """Implementation for relay list with command metadata attached."""
    from rich.box import ROUNDED
    from rich.table import Table

    cluster = load_cluster()

    relays = cluster.relays
    if exit_arg:
        try:
            exit_ip = find_exit_node(cluster, exit_arg)
        except ValueError as exc:
            fail(str(exc), hint="List nodes: meridian node list", hint_type="user")
        relays = [r for r in relays if r.exit_node_ip == exit_ip]

    if not relays:
        info(f"No relays {'attached to exit ' + exit_arg if exit_arg else 'configured'}")
        err_console.print("\n  [dim]Deploy one: meridian relay deploy RELAY_IP --exit EXIT_IP[/dim]\n")
        return

    # Optionally check host status from panel
    host_status: dict[str, bool | None] = {}
    try:
        with make_panel(cluster) as panel:
            host_map = {h.uuid: h for h in panel.list_hosts()}
            for relay in relays:
                for _, host_uuid in relay.host_uuids.items():
                    h = host_map.get(host_uuid)
                    if h and (relay.ip not in host_status or not host_status[relay.ip]):
                        host_status[relay.ip] = not h.is_disabled
    except RemnawaveError:
        pass

    # JSON output
    from meridian.console import is_json_mode

    if is_json_mode():
        from meridian.renderers import emit_json

        relays_data = []
        for relay in relays:
            enabled = host_status.get(relay.ip)
            relays_data.append(
                {
                    "ip": relay.ip,
                    "name": relay.name,
                    "exit_node_ip": relay.exit_node_ip,
                    "port": relay.port,
                    "sni": relay.sni,
                    "enabled": enabled,
                }
            )
        count = len(relays_data)
        emit_json(
            command_envelope(
                command="relay.list",
                data={"relays": relays_data},
                summary=Summary(
                    text=f"{count} relay(s) configured",
                    changed=False,
                    counts={"relays": count},
                ),
                timer=operation.timer,
            )
        )
        return

    title = f"Relays for {exit_arg}" if exit_arg else "All Relay Nodes"
    table = Table(title=title, show_lines=False, pad_edge=False, box=ROUNDED, padding=(0, 2))
    for col, kw in [
        ("Relay IP", {"style": "bold cyan"}),
        ("Name", {"style": "dim"}),
        ("Exit", {"style": "bold"}),
        ("Port", {"justify": "right"}),
        ("SNI", {"style": "dim"}),
        ("Status", {"justify": "center"}),
    ]:
        table.add_column(col, **kw)  # type: ignore[arg-type]

    for relay in relays:
        enabled = host_status.get(relay.ip)
        if enabled:
            status_str = "[green]enabled[/green]"
        elif enabled is False:
            status_str = "[dim]disabled[/dim]"
        else:
            status_str = "[dim]-[/dim]"
        table.add_row(
            relay.ip,
            relay.name or "-",
            relay.exit_node_ip,
            str(relay.port),
            relay.sni or "-",
            status_str,
        )

    err_console.print()
    err_console.print(table)
    err_console.print()
    err_console.print(f"  [dim]Total: {len(relays)} relay(s)[/dim]\n")


def run_remove(
    relay_ip: str,
    exit_arg: str = "",
    user: str = "",
    yes: bool = False,
) -> None:
    """Remove a relay node."""
    request = validate_command_input(
        RelayTargetRequest,
        "Invalid relay remove request",
        relay_ip=relay_ip,
        exit_arg=exit_arg,
        user=user,
        yes=yes,
    )

    cluster = load_cluster()

    # Find relay entry
    relay_entry = cluster.find_relay(request.relay_ip)
    if relay_entry is None:
        fail(f"Relay {request.relay_ip} not found in cluster", hint="Check: meridian relay list", hint_type="user")

    # Verify exit_arg matches if specified
    if request.exit_arg:
        try:
            resolved_exit = find_exit_node(cluster, request.exit_arg)
        except ValueError as exc:
            fail(str(exc), hint="List nodes: meridian node list", hint_type="user")
        if relay_entry.exit_node_ip != resolved_exit:
            fail(
                f"Relay {request.relay_ip} is attached to exit {relay_entry.exit_node_ip}, not {request.exit_arg}",
                hint_type="user",
            )

    if not request.yes:
        relay_label = relay_entry.name or request.relay_ip
        if not confirm(f"Remove relay {relay_label} from exit {relay_entry.exit_node_ip}?"):
            raise typer.Exit(1)

    relay_user = request.user or relay_entry.ssh_user or "root"

    # Delete Remnawave hosts
    with make_panel(cluster) as panel:
        info("Removing relay host entries from panel...")
        delete_relay_hosts(panel, relay_entry)

    # Remove nginx config from exit server
    exit_node = cluster.find_node(relay_entry.exit_node_ip)
    if relay_entry.sni and exit_node:
        info("Removing relay nginx config from exit server...")
        try:
            exit_conn = ServerConnection(ip=exit_node.ip, user=exit_node.ssh_user, port=exit_node.ssh_port)
            exit_conn.check_ssh(ui=RichSSHUI())
            if not remove_relay_nginx(exit_conn, relay_entry):
                warn("Relay nginx cleanup failed -- manual cleanup may be needed")
        except SSHError:
            warn(f"Could not connect to exit node {exit_node.ip} -- nginx not cleaned up")

    # Stop service on relay
    info(f"Stopping relay service on {request.relay_ip}...")
    try:
        relay_conn = ServerConnection(ip=request.relay_ip, user=relay_user, port=relay_entry.ssh_port)
        relay_conn.check_ssh(ui=RichSSHUI())
        relay_conn.run(f"systemctl stop {RELAY_SERVICE_NAME} 2>/dev/null", timeout=15)
        relay_conn.run(f"systemctl disable {RELAY_SERVICE_NAME} 2>/dev/null", timeout=10)
        ok("Relay service stopped")
    except (SSHError, OSError):
        warn(f"Could not connect to relay {request.relay_ip} -- service may still be running")

    # Remove from cluster.yml and local state
    cluster.relays = [r for r in cluster.relays if r.ip != request.relay_ip]
    cluster.backup()
    cluster.save()

    # Hybrid sync — drop from desired_relays (only if managed declaratively).
    from meridian.reconciler.snapshots import hybrid_sync_desired_relays_remove

    hybrid_sync_desired_relays_remove(cluster, request.relay_ip)

    ok(f"Relay {request.relay_ip} removed")
    err_console.print()


def run_check(
    relay_ip: str,
    exit_arg: str = "",
    user: str = "",
) -> None:
    """Check health of a relay node."""
    request = validate_command_input(
        RelayTargetRequest,
        "Invalid relay check request",
        relay_ip=relay_ip,
        exit_arg=exit_arg,
        user=user,
    )

    cluster = load_cluster()

    relay_entry = cluster.find_relay(request.relay_ip)
    if relay_entry is None:
        fail(f"Relay {request.relay_ip} not found in cluster", hint="Check: meridian relay list", hint_type="user")

    info(f"Checking relay: {relay_entry.name or request.relay_ip} -> exit: {relay_entry.exit_node_ip}")
    err_console.print()
    all_ok = True
    relay_user = request.user or relay_entry.ssh_user or "root"

    # 1. SSH connectivity to relay
    try:
        relay_conn = ServerConnection(ip=request.relay_ip, user=relay_user, port=relay_entry.ssh_port)
        relay_conn.check_ssh(ui=RichSSHUI())
        ok("SSH to relay: connected")
    except (SSHError, OSError):
        err_console.print(f"  [red bold]x[/red bold] SSH to relay: failed ({request.relay_ip})")
        warn("Cannot proceed without SSH -- check SSH key and user")
        return

    # 2. Realm service status
    status = relay_conn.run(f"systemctl is-active {RELAY_SERVICE_NAME}", timeout=10)
    if status.returncode == 0 and status.stdout.strip() == "active":
        ok("Realm service: active")
    else:
        err_console.print(f"  [red bold]x[/red bold] Realm service: {status.stdout.strip() or 'not found'}")
        all_ok = False

    # 3. Relay -> exit TCP connectivity
    q_exit = shlex.quote(relay_entry.exit_node_ip)
    tcp_test = relay_conn.run(f"nc -z -w 5 {q_exit} 443 2>/dev/null", timeout=10)
    if tcp_test.returncode == 0:
        ok(f"Relay -> exit TCP: reachable ({relay_entry.exit_node_ip}:443)")
    else:
        err_console.print("  [red bold]x[/red bold] Relay -> exit TCP: unreachable")
        all_ok = False

    # 4. Local -> relay TCP connectivity
    from meridian.health import tcp_connect

    if tcp_connect(request.relay_ip, relay_entry.port):
        ok(f"Local -> relay TCP: reachable ({request.relay_ip}:{relay_entry.port})")
    else:
        err_console.print("  [red bold]x[/red bold] Local -> relay TCP: unreachable")
        all_ok = False

    # 5. Panel host status
    try:
        with make_panel(cluster) as panel:
            host_map = {h.uuid: h for h in panel.list_hosts()}
            for proto_key, host_uuid in relay_entry.host_uuids.items():
                host = host_map.get(host_uuid)
                if host and not host.is_disabled:
                    ok(f"Panel host ({proto_key}): enabled")
                elif host:
                    err_console.print(f"  [yellow]![/yellow] Panel host ({proto_key}): disabled")
                    all_ok = False
                else:
                    err_console.print(f"  [red bold]x[/red bold] Panel host ({proto_key}): not found")
                    all_ok = False
    except RemnawaveError:
        warn("Could not check panel host status -- panel unreachable")

    err_console.print()
    ok("All checks passed") if all_ok else warn("Some checks failed -- see above")
    err_console.print()
