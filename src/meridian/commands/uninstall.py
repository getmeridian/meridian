"""Remove proxy from server."""

from __future__ import annotations

import typer

from meridian.cluster import ClusterConfig
from meridian.commands._helpers import load_cluster
from meridian.commands.resolve import (
    ensure_server_connection,
    resolve_server,
)
from meridian.config import SERVER_PROFILES_FILE
from meridian.console import confirm, err_console, fail, info, ok, warn
from meridian.core.errors import LocalStateError
from meridian.remnawave import RemnawaveError
from meridian.servers import ServerEntry, ServerRegistry
from meridian.ssh import ServerConnection, SSHError
from meridian.ssh_ui import RichSSHUI


def _remove_saved_server_or_fail(registry: ServerRegistry, server_ip: str) -> None:
    """Remove a stale connection profile after teardown, with a truthful partial failure."""
    try:
        registry.remove(server_ip)
    except LocalStateError as exc:
        fail(
            "Remote teardown completed, but the saved server profile was retained",
            hint=f"{exc} Remove it with `meridian server remove {server_ip}` after repairing local state.",
            hint_type=exc.category,
        )
    except OSError as exc:
        fail(
            "Remote teardown completed, but the saved server profile was retained",
            hint=f"{exc} Check ~/.meridian permissions, then run `meridian server remove {server_ip}`.",
            hint_type="system",
        )


def _v4_target_references(cluster: ClusterConfig, entry: ServerEntry) -> list[str]:
    """Return current V4 roles that still require a saved server."""
    intent = cluster.topology_intent
    if intent is None:
        return []
    identities = {entry.id, entry.name, entry.host}
    references: list[str] = []
    if intent.control.server_ref in identities:
        references.append("control plane")
    references.extend(f"exit {item.id}" for item in intent.exits if item.server_ref in identities)
    references.extend(
        f"relay {item.id}" for item in intent.transparent_relays if identities.intersection(item.hop_server_refs)
    )
    references.extend(f"routing gateway {item.id}" for item in intent.routing_gateways if item.server_ref in identities)
    for workload in cluster.workloads:
        if workload.active and identities.intersection(workload.server_refs):
            references.append(f"active workload {workload.id}")
    return list(dict.fromkeys(references))


def run(
    ip: str = "",
    user: str = "root",
    yes: bool = False,
    requested_server: str = "",
) -> None:
    """Uninstall Meridian from a server."""
    registry = ServerRegistry(SERVER_PROFILES_FILE)

    resolved = resolve_server(registry, requested_server=requested_server, explicit_ip=ip, user=user)

    # Teardown mutates legacy topology directly. V4 topology must be changed
    # through its reviewed intent so saved roles cannot diverge from reality.
    cluster = load_cluster(require_configured=False)
    if cluster.topology_intent is not None:
        try:
            saved_server = registry.find(resolved.ip)
        except LocalStateError as exc:
            fail(exc)
        if saved_server is None:
            fail(
                f"Cannot prove V4 role ownership for {resolved.ip}",
                hint="Restore its saved server profile before attempting teardown.",
                hint_type="user",
            )
        references = _v4_target_references(cluster, saved_server)
        if references:
            fail(
                f"Server {resolved.ip} is still managed by V4 topology: {', '.join(references)}",
                hint="Remove those roles in `meridian setup`, apply the reviewed topology, then retry teardown.",
                hint_type="user",
            )

    node = cluster.find_node(resolved.ip)
    is_panel_host = cluster.panel.server_ip == resolved.ip or bool(node and node.is_panel_host)
    relay_target = cluster.find_relay(resolved.ip)
    if relay_target is not None and (node is not None or is_panel_host):
        fail(
            f"Server {resolved.ip} has both relay and node roles",
            hint="Remove the relay role with `meridian relay remove`, then run teardown again for the node.",
            hint_type="user",
        )
    remaining_nodes = [candidate for candidate in cluster.nodes if candidate.ip != resolved.ip]
    remaining_relays = list(cluster.relays)
    if is_panel_host and (remaining_nodes or remaining_relays):
        dependents = [
            *[candidate.name or candidate.ip for candidate in remaining_nodes],
            *[relay.name or relay.ip for relay in remaining_relays],
        ]
        fail(
            "Cannot teardown the panel while other deployed servers remain",
            hint=f"Teardown or remove these deployments first: {', '.join(dependents)}.",
            hint_type="user",
        )

    # Confirm only after proving that ownership and dependency guards permit
    # the selected teardown.
    err_console.print()
    warn(f"This will remove Meridian from {resolved.ip}.")
    warn("Docker and system packages will NOT be touched.")
    if not yes and not confirm("Continue?"):
        info("Cancelled.")
        raise typer.Exit(1)
    err_console.print()

    if relay_target is not None:
        from meridian.commands.relay import run_remove as remove_relay_command

        remove_relay_command(relay_target.ip, user=resolved.user, yes=True)
        _remove_saved_server_or_fail(registry, resolved.ip)
        err_console.print()
        ok("Relay teardown complete.")
        err_console.print()
        return

    resolved = ensure_server_connection(resolved)

    info(f"Removing Meridian from {resolved.ip}...")
    err_console.print()

    # Relays must stop advertising and forwarding to an exit before that exit
    # disappears. When the panel itself is being removed, stopping Realm is
    # sufficient because the control plane and its Host records disappear too.
    relays_for_node = [r for r in cluster.relays if r.exit_node_ip == resolved.ip]
    if relays_for_node and is_panel_host:
        from meridian.relay_ops import stop_relay_service

        info(f"Stopping {len(relays_for_node)} relay node(s)...")
        failed_relays: list[str] = []
        for relay in relays_for_node:
            try:
                relay_conn = ServerConnection(ip=relay.ip, user=relay.ssh_user, port=relay.ssh_port)
                relay_conn.check_ssh(ui=RichSSHUI())
                if stop_relay_service(relay_conn):
                    ok(f"Relay {relay.ip} stopped")
                else:
                    failed_relays.append(relay.ip)
                    warn(f"Could not stop relay {relay.ip}")
            except (OSError, RuntimeError, SSHError):
                failed_relays.append(relay.ip)
                warn(f"Could not reach relay {relay.ip} — service may still be running")
        err_console.print()
        if failed_relays:
            failed_text = ", ".join(failed_relays)
            fail(
                f"Teardown stopped because relay cleanup failed: {failed_text}",
                hint="Restore relay SSH access and retry; local state and the exit deployment were retained.",
                hint_type="system",
            )
    elif relays_for_node:
        from meridian.commands._helpers import make_panel
        from meridian.operations import remove_relay

        info(f"Removing {len(relays_for_node)} dependent relay node(s)...")
        try:
            with make_panel(cluster) as panel:
                for relay in relays_for_node:
                    remove_relay(cluster, panel, relay_ip=relay.ip)
                    ok(f"Relay {relay.ip} removed")
        except (OSError, RuntimeError, SSHError, RemnawaveError) as exc:
            fail(
                f"Teardown stopped because relay cleanup failed: {exc}",
                hint="Restore relay, exit, and panel access, then retry; unfinished topology state was retained.",
                hint_type="system",
            )
        err_console.print()

    # Run uninstall via provisioner
    from meridian.provision.progress import RichStepRenderer
    from meridian.provision.steps import ProvisionContext, Provisioner
    from meridian.provision.uninstall import Uninstall

    ctx = ProvisionContext(
        ip=resolved.ip,
        user=resolved.user,
    )

    provisioner = Provisioner([Uninstall()])
    results = provisioner.run(resolved.conn, ctx, renderer=RichStepRenderer())

    failed = [r for r in results if r.status == "failed"]
    if failed:
        fail("Uninstall failed", hint=failed[0].detail, hint_type="system")

    # Reconcile panel and local topology only after remote uninstall succeeds.
    # A failed panel delete leaves the local node entry as the retry record;
    # rerunning the remote uninstall is idempotent.
    from meridian.config import CLUSTER_CONFIG

    if cluster.is_configured:
        if is_panel_host:
            # Panel host torn down — entire cluster config is invalid
            try:
                cluster.backup()
                CLUSTER_CONFIG.unlink(missing_ok=True)
            except (LocalStateError, OSError) as exc:
                fail(
                    "Remote panel-host teardown completed, but cluster.yml could not be archived or removed",
                    hint=(
                        f"{exc} The saved cluster is now stale; repair local permissions, then archive or remove it "
                        "before another deployment. The saved server profile was retained."
                    ),
                    hint_type="system",
                )
            info("Removed cluster.yml (panel host was torn down)")
        else:
            from meridian.commands._helpers import make_panel
            from meridian.operations import remove_node

            try:
                with make_panel(cluster) as panel:
                    remove_node(
                        cluster,
                        panel,
                        node_ip=resolved.ip,
                        force=True,
                        cleanup=lambda _node: True,
                    )
            except RemnawaveError as exc:
                fail(
                    f"Remote uninstall completed, but panel cleanup failed: {exc}",
                    hint="Retry teardown when the panel is available; local state was retained.",
                    hint_type="system",
                )
            info(f"Removed node {resolved.ip} from panel and cluster.yml")

    # Registry deletion is last: a stale saved connection is recoverable, but
    # deleting it before topology persistence would discard retry information.
    _remove_saved_server_or_fail(registry, resolved.ip)

    err_console.print()
    ok("Uninstall complete.")
    err_console.print()
    err_console.print(f"  [dim]To redeploy: meridian deploy {resolved.ip}[/dim]")
    err_console.print()
