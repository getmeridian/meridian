"""Relay management -- deploy, list, remove, check relay nodes.

Relays are lightweight Realm TCP forwarders that provide domestic entry
points. All relay-to-panel mapping uses Remnawave Host entries; topology
is persisted in cluster.yml.
"""

from __future__ import annotations

import re
import shlex

import typer
from rich.markup import escape

from meridian.adapters.cluster import topology_from_local_cluster
from meridian.cluster import ClusterConfig, RelayEntry
from meridian.commands._helpers import (
    load_cluster,
    make_panel,
    persist_reviewed_apply,
    remote_mutation_persistence,
    reviewed_apply_persistence,
)
from meridian.commands._validation import validate_command_input
from meridian.commands.relay_rendering import render_relay_deployment_plan, render_relay_deployment_success
from meridian.config import RELAY_SERVICE_NAME
from meridian.console import confirm, err_console, fail, info, ok, warn
from meridian.core.command_inputs import RelayDeployRequest, RelayTargetRequest
from meridian.core.errors import LocalStateError
from meridian.core.errors import MeridianError as MeridianException
from meridian.core.fleet import build_relay_list_result
from meridian.core.models import MeridianError, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.diagnostics import command_evidence_unavailable
from meridian.relay_ops import (
    create_relay_hosts,
    delete_relay_hosts,
    deploy_relay_nginx,
    find_exit_node,
    remove_relay_nginx,
    stop_relay_service,
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
    q_listen_port = shlex.quote(str(request.listen_port))
    port_check = relay_conn.run(f"ss -tlnp sport = :{q_listen_port} 2>/dev/null", timeout=10)
    replace_existing_realm = False
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
            replace_existing_realm = True
            warn(f"Previous relay service found on port {request.listen_port}; this deploy will replace it")
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

    render_relay_deployment_plan(request, exit_ip=exit_ip, relay_sni=relay_sni)

    if not request.yes:
        if not confirm(f"Deploy relay to {request.user}@{request.relay_ip}?"):
            raise typer.Exit(1)

    if replace_existing_realm:
        if not stop_relay_service(relay_conn):
            fail(
                "Could not stop the previous relay service",
                hint="The existing relay was left in place. Fix systemd access and retry.",
                hint_type="system",
            )
        ok("Previous relay service stopped")

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
        try:
            host_uuids = create_relay_hosts(
                panel,
                cluster,
                request.relay_ip,
                request.listen_port,
                relay_sni,
                request.relay_name,
            )
        except RemnawaveError as exc:
            fail(
                "Relay host reconciliation failed after Realm was deployed remotely",
                hint=f"{exc} Remote relay state changed; repair panel hosts, then retry deployment.",
                hint_type="system",
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
    with remote_mutation_persistence(f"Relay {request.relay_ip} was deployed remotely"):
        cluster.backup()
        cluster.relays.append(relay_entry)
        cluster.save()

        # Mirror into desired_relays when that legacy list is managed.
        from meridian.reconciler.snapshots import hybrid_sync_desired_relays_add

        exit_node_for_sync = cluster.find_node(exit_ip)
        exit_ref = exit_node_for_sync.name if exit_node_for_sync and exit_node_for_sync.name else exit_ip
        hybrid_sync_desired_relays_add(cluster, relay_entry, exit_node_ref=exit_ref)

    ok("Relay saved to cluster")

    render_relay_deployment_success(request, exit_ip=exit_ip)


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
    try:
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
    except LocalStateError as exc:
        fail(exc)
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
    try:
        registry.add(
            ServerEntry(
                host=request.relay_ip,
                user=request.user,
                name=request.relay_name or request.relay_ip,
                port=request.ssh_port,
                auth_state="validated",
            )
        )
    except LocalStateError as exc:
        fail(exc)
    except OSError as exc:
        fail(
            f"Could not save the V4 relay server profile: {exc}",
            hint="Check ~/.meridian permissions and disk space, then retry.",
            hint_type="system",
        )
    try:
        entry = registry.find(request.relay_ip)
    except LocalStateError as exc:
        fail(exc)
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
    try:
        with reviewed_apply_persistence("V4 relay apply"):
            result = SetupRuntime(
                registry,
                cluster_loader=lambda: cluster,
                persist=persist_reviewed_apply,
            ).apply_intent(updated)
    except MeridianException as exc:
        fail(exc)
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
) -> None:
    """List relay nodes from cluster configuration."""
    from meridian.console import error_context

    operation = OperationContext()
    with error_context("relay.list", timer=operation.timer):
        _run_list(exit_arg=exit_arg, operation=operation)


def _run_list(
    *,
    exit_arg: str = "",
    operation: OperationContext,
) -> None:
    """Implementation for relay list with command metadata attached."""
    from rich.box import ROUNDED
    from rich.table import Table

    cluster = load_cluster()
    try:
        topology = topology_from_local_cluster(cluster)
    except LocalStateError as exc:
        fail(exc)

    relays = topology.relays
    if exit_arg:
        exit_node = next(
            (node for node in topology.nodes if node.ip == exit_arg or node.name == exit_arg),
            None,
        )
        if exit_node is None:
            fail(
                f"Exit node '{exit_arg}' not found in cluster",
                hint="List nodes: meridian node list",
                hint_type="user",
            )
        exit_ip = exit_node.ip
        relays = [r for r in relays if r.exit_node_ip == exit_ip]

    from meridian.console import is_json_mode

    if not relays and not is_json_mode():
        info(f"No relays {'attached to exit ' + exit_arg if exit_arg else 'configured'}")
        err_console.print("\n  [dim]Deploy one: meridian relay deploy RELAY_IP --exit EXIT_IP[/dim]\n")
        return

    # Optionally check host status from panel
    host_status: dict[tuple[str, int], bool | None] = {}
    warnings: list[MeridianError] = []
    if relays:
        try:
            with make_panel(cluster) as panel:
                host_map = {h.uuid: h for h in panel.list_hosts()}
                for relay in relays:
                    endpoint = (relay.ip, relay.port)
                    for host_ref in relay.host_refs:
                        h = host_map.get(host_ref.uuid)
                        if h and (endpoint not in host_status or not host_status[endpoint]):
                            host_status[endpoint] = not h.is_disabled
        except RemnawaveError as exc:
            warnings.append(
                MeridianError(
                    code="MERIDIAN_RELAY_HOST_STATUS_UNAVAILABLE",
                    category="system",
                    message="Relay topology is available, but panel host status could not be collected.",
                    hint=exc.hint or "Check panel connectivity and retry.",
                    retryable=True,
                    exit_code=3,
                )
            )
            if not is_json_mode():
                warn("Panel host status is unavailable; relay rows are shown as unknown")

    # JSON output
    if is_json_mode():
        from meridian.renderers import emit_json

        result = build_relay_list_result(relays, host_status)
        count = len(result.relays)
        exit_code = 3 if warnings else 0
        emit_json(
            command_envelope(
                command="relay.list",
                data=result.to_data(),
                summary=Summary(
                    text=f"{count} relay(s) configured",
                    changed=False,
                    counts={"relays": count},
                ),
                exit_code=exit_code,
                warnings=warnings,
                timer=operation.timer,
            )
        )
        if exit_code:
            raise typer.Exit(exit_code)
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
        enabled = host_status.get((relay.ip, relay.port))
        if enabled:
            status_str = "[green]enabled[/green]"
        elif enabled is False:
            status_str = "[dim]disabled[/dim]"
        else:
            status_str = "[dim]-[/dim]"
        table.add_row(
            escape(relay.ip),
            escape(relay.name) if relay.name else "-",
            escape(relay.exit_node_ip),
            str(relay.port),
            escape(relay.sni) if relay.sni else "-",
            status_str,
        )

    err_console.print()
    err_console.print(table)
    err_console.print()
    err_console.print(f"  [dim]Total: {len(relays)} relay(s)[/dim]\n")
    if warnings:
        raise typer.Exit(3)


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

    if cluster.topology_intent is not None:
        try:
            topology = topology_from_local_cluster(cluster)
        except LocalStateError as exc:
            fail(exc)
        projected = next((relay for relay in topology.relays if relay.ip == request.relay_ip), None)
        if projected is not None:
            fail(
                f"Relay {request.relay_ip} is managed by V4 topology",
                hint="Remove the relay chain in `meridian setup`, then apply the reviewed plan.",
                hint_type="user",
            )

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

    # Stop Realm first. Until this succeeds the panel and exit routing are
    # left untouched, and cluster.yml remains the retry record.
    info(f"Stopping relay service on {request.relay_ip}...")
    try:
        relay_conn = ServerConnection(ip=request.relay_ip, user=relay_user, port=relay_entry.ssh_port)
        relay_conn.check_ssh(ui=RichSSHUI())
        if not stop_relay_service(relay_conn):
            fail(
                f"Could not stop relay service on {request.relay_ip}",
                hint="Fix the remote service and retry; cluster state was retained.",
                hint_type="system",
            )
        from meridian.relay_ops import remove_relay_artifacts

        if not remove_relay_artifacts(relay_conn, listen_port=relay_entry.port):
            fail(
                f"Could not remove relay artifacts from {request.relay_ip}",
                hint="Fix remote filesystem/systemd access and retry; cluster state was retained.",
                hint_type="system",
            )
        ok("Relay service stopped")
    except (OSError, SSHError) as exc:
        fail(
            f"Could not connect to relay {request.relay_ip}",
            hint=f"{exc} Cluster state was retained; retry when SSH is available.",
            hint_type="system",
        )

    # Remove nginx config from exit server
    exit_node = cluster.find_node(relay_entry.exit_node_ip)
    if relay_entry.sni and exit_node is None:
        fail(
            f"Cannot clean up relay routing: exit node {relay_entry.exit_node_ip} is missing",
            hint="Restore the exit node entry and retry; cluster state was retained.",
            hint_type="system",
        )
    if relay_entry.sni and exit_node and relay_entry.sni != (exit_node.sni or ""):
        info("Removing relay nginx config from exit server...")
        try:
            exit_conn = ServerConnection(ip=exit_node.ip, user=exit_node.ssh_user, port=exit_node.ssh_port)
            exit_conn.check_ssh(ui=RichSSHUI())
            if not remove_relay_nginx(exit_conn, relay_entry):
                fail(
                    "Relay nginx cleanup failed on the exit server",
                    hint="Fix nginx and retry; cluster state was retained.",
                    hint_type="system",
                )
        except (OSError, SSHError) as exc:
            fail(
                f"Could not connect to exit node {exit_node.ip}",
                hint=f"{exc} Cluster state was retained; retry when SSH is available.",
                hint_type="system",
            )

    # Delete panel hosts last. Missing hosts are idempotent success; an API
    # failure leaves the relay entry available for another attempt.
    with make_panel(cluster) as panel:
        info("Removing relay host entries from panel...")
        if not delete_relay_hosts(panel, relay_entry):
            fail(
                "Could not remove all relay host entries from the panel",
                hint="Retry when the panel is available; cluster state was retained.",
                hint_type="system",
            )

    # Remove from cluster.yml only after every required remote cleanup passed.
    with remote_mutation_persistence(f"Relay {request.relay_ip} was removed remotely"):
        cluster.relays = [r for r in cluster.relays if r.ip != request.relay_ip]
        cluster.backup()
        cluster.save()

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
    try:
        topology = topology_from_local_cluster(cluster)
    except LocalStateError as exc:
        fail(exc)
    relay_entry = next((relay for relay in topology.relays if relay.ip == request.relay_ip), None)
    if relay_entry is None:
        fail(f"Relay {request.relay_ip} not found in cluster", hint="Check: meridian relay list", hint_type="user")
    if cluster.topology_intent is not None:
        fail(
            f"Relay {request.relay_ip} is a V4 managed chain",
            hint=(
                "Per-hop V4 relay checking is not available yet. Use `meridian test` for end-to-end route health "
                "and `meridian setup` to review the chain."
            ),
            hint_type="user",
        )

    if request.exit_arg:
        exit_node = next(
            (node for node in topology.nodes if request.exit_arg in {node.ip, node.name}),
            None,
        )
        if exit_node is None:
            fail(
                f"Exit node '{request.exit_arg}' not found in cluster",
                hint="List nodes: meridian node list",
                hint_type="user",
            )
        if relay_entry.exit_node_ip != exit_node.ip:
            fail(
                f"Relay {request.relay_ip} is attached to exit {relay_entry.exit_node_ip}, not {request.exit_arg}",
                hint_type="user",
            )

    info(f"Checking relay: {relay_entry.name or request.relay_ip} -> exit: {relay_entry.exit_node_ip}")
    err_console.print()
    exit_code = 0
    relay_user = request.user or relay_entry.ssh_user or "root"

    # 1. SSH connectivity to relay
    try:
        relay_conn = ServerConnection(ip=request.relay_ip, user=relay_user, port=relay_entry.ssh_port)
        relay_conn.check_ssh(ui=RichSSHUI())
        ok("SSH to relay: connected")
    except (SSHError, OSError):
        err_console.print(f"  [yellow]![/yellow] SSH to relay: unavailable ({request.relay_ip})")
        warn("Cannot proceed without SSH -- check SSH key and user")
        raise typer.Exit(3)

    # 2. Realm service status
    q_service = shlex.quote(RELAY_SERVICE_NAME)
    try:
        status = relay_conn.run(f"systemctl is-active {q_service}", timeout=10)
    except (OSError, SSHError):
        status = None
    if status is None:
        warn("Realm service: status evidence unavailable")
        exit_code = max(exit_code, 3)
    elif status.returncode == 0 and status.stdout.strip() == "active":
        ok("Realm service: active")
    elif command_evidence_unavailable(status.returncode):
        warn("Realm service: status evidence unavailable")
        exit_code = max(exit_code, 3)
    else:
        err_console.print(f"  [red bold]x[/red bold] Realm service: {escape(status.stdout.strip() or 'not found')}")
        exit_code = 4

    # 3. Relay -> exit TCP connectivity
    q_exit = shlex.quote(relay_entry.exit_node_ip)
    try:
        tcp_test = relay_conn.run(f"nc -z -w 5 {q_exit} 443 2>/dev/null", timeout=10)
    except (OSError, SSHError):
        tcp_test = None
    if tcp_test is None:
        warn("Relay -> exit TCP: connectivity evidence unavailable")
        exit_code = max(exit_code, 3)
    elif tcp_test.returncode == 0:
        ok(f"Relay -> exit TCP: reachable ({relay_entry.exit_node_ip}:443)")
    elif command_evidence_unavailable(tcp_test.returncode):
        warn("Relay -> exit TCP: connectivity evidence unavailable")
        exit_code = max(exit_code, 3)
    else:
        err_console.print("  [red bold]x[/red bold] Relay -> exit TCP: unreachable")
        exit_code = 4

    # 4. Local -> relay TCP connectivity
    from meridian.health import tcp_connect

    if tcp_connect(request.relay_ip, relay_entry.port):
        ok(f"Local -> relay TCP: reachable ({request.relay_ip}:{relay_entry.port})")
    else:
        err_console.print("  [red bold]x[/red bold] Local -> relay TCP: unreachable")
        exit_code = 4

    # 5. Panel host status
    try:
        with make_panel(cluster) as panel:
            host_map = {h.uuid: h for h in panel.list_hosts()}
            if not relay_entry.host_refs:
                warn("Panel host bindings are unavailable for this relay")
                exit_code = max(exit_code, 3)
            for host_ref in relay_entry.host_refs:
                host = host_map.get(host_ref.uuid)
                if host and not host.is_disabled:
                    ok(f"Panel host ({host_ref.protocol}): enabled")
                elif host:
                    err_console.print(f"  [yellow]![/yellow] Panel host ({escape(host_ref.protocol)}): disabled")
                    exit_code = 4
                else:
                    err_console.print(f"  [red bold]x[/red bold] Panel host ({escape(host_ref.protocol)}): not found")
                    exit_code = 4
    except RemnawaveError:
        warn("Could not check panel host status -- panel unreachable")
        exit_code = max(exit_code, 3)

    err_console.print()
    if exit_code == 0:
        ok("All checks passed")
    elif exit_code == 3:
        warn("Checks were inconclusive -- required evidence was unavailable")
    else:
        warn("Some checks failed -- see above")
    err_console.print()
    if exit_code:
        raise typer.Exit(exit_code)
