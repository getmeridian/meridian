"""Remove Meridian from a server."""

from __future__ import annotations

import typer

from meridian import config
from meridian.cluster import ClusterConfig
from meridian.commands import teardown_backup
from meridian.commands._helpers import load_cluster, load_topology_cluster
from meridian.commands.resolve import (
    ensure_server_connection,
    resolve_server,
)
from meridian.compiler.deployment import deployment_server_refs
from meridian.console import confirm, err_console, fail, info, ok, warn
from meridian.core.errors import LocalStateError, previous_release_command
from meridian.servers import ServerEntry, ServerRegistry


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
            hint=f"{exc} Check {config.HOME_DISPLAY} permissions, then run `meridian server remove {server_ip}`.",
            hint_type="system",
        )


def _topology_references(cluster: ClusterConfig, entry: ServerEntry) -> list[str]:
    """Return current topology roles that still require a saved server."""
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
    user: str = "",
    yes: bool = False,
    *,
    ssh_port: int = 0,
    discard_backup: bool = False,
) -> None:
    """Uninstall Meridian from a server."""
    registry = ServerRegistry(config.SERVER_PROFILES_FILE)

    resolved = resolve_server(registry, explicit_ip=ip, user=user, port=ssh_port)

    # Roles change only through the reviewed topology, so teardown refuses a
    # server that still carries one -- unless it is the whole fleet.
    cluster = load_cluster()
    if cluster.is_configured and cluster.topology_intent is None:
        load_topology_cluster()  # refuses with rebuild guidance
    control_in_target = False
    if cluster.topology_intent is not None:
        try:
            saved_server = registry.find(resolved.ip)
        except LocalStateError as exc:
            fail(exc)
        if saved_server is None:
            fail(
                f"Cannot tell which roles {resolved.ip} has in your topology",
                hint="Restore its saved server profile before attempting teardown.",
                hint_type="user",
            )
        references = _topology_references(cluster, saved_server)
        entire_fleet = deployment_server_refs(cluster.topology_intent) == {saved_server.id}
        control_in_target = entire_fleet and "control plane" in references
        if references and not entire_fleet:
            hint = "Remove those roles in `meridian setup`, apply the reviewed topology, then retry teardown."
            if cluster.topology_intent.control.backend == "remnawave":
                # `setup` and `node remove` refuse a Remnawave fleet; only its own release still edits it.
                hint = (
                    "This fleet still runs Remnawave: move it first (meridian migrate), or take it apart with "
                    f"the release that deployed it: {previous_release_command(cluster.panel.deployed_with, 'setup')}"
                )
            fail(
                f"Server {resolved.ip} still has roles in your topology: {', '.join(references)}",
                hint=hint,
                hint_type="user",
            )
    is_panel_host = control_in_target
    if is_panel_host and not cluster.runs_control_service:
        fail(
            "This controller uses the previous panel and cannot confirm a Meridian recovery copy.",
            hint="Keep its database backup and retire it with the release that deployed it, or migrate first.",
            hint_type="user",
        )
    if discard_backup and not is_panel_host:
        fail("--discard-backup only applies to this fleet’s last control server.", hint_type="user")
    current = teardown_backup.status(cluster) if is_panel_host else None

    # Confirm only after proving that ownership and dependency guards permit
    # the selected teardown.
    err_console.print()
    warn(f"This will remove Meridian from {resolved.ip}.")
    if is_panel_host:
        teardown_backup.show_review(current, discard=discard_backup)
    info("System packages, SSH hardening, and services outside this fleet stay in place.")
    if not yes and not confirm("Remove Meridian from this server?", default=False):
        info("Cancelled.")
        raise typer.Exit(1)
    err_console.print()

    resolved = ensure_server_connection(resolved)

    generation = (
        teardown_backup.confirmed_generation(cluster, current) if is_panel_host and not discard_backup else None
    )
    info(f"Removing Meridian from {resolved.ip}...")
    err_console.print()

    from meridian.provision.progress import RichStepRenderer
    from meridian.provision.steps import ProvisionContext, Provisioner
    from meridian.provision.uninstall import Uninstall

    ctx = ProvisionContext(
        ip=resolved.ip,
        user=resolved.user,
        cluster=cluster,
    )

    provisioner = Provisioner([Uninstall(controller=is_panel_host, generation=generation, discard=discard_backup)])
    results = provisioner.run(resolved.conn, ctx, renderer=RichStepRenderer())

    failed = [r for r in results if r.status == "failed"]
    if failed:
        fail("Uninstall failed", hint=failed[0].detail, hint_type="system")

    # Reconcile panel and local topology only after remote uninstall succeeds.
    # A failed panel delete leaves the local node entry as the retry record;
    # rerunning the remote uninstall is idempotent.
    if cluster.is_configured:
        if is_panel_host:
            # Panel host torn down — entire cluster config is invalid
            try:
                cluster.backup()
                config.CLUSTER_CONFIG.unlink(missing_ok=True)
                config.SETUP_DRAFT_FILE.unlink(missing_ok=True)
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

    # Registry deletion is last: a stale saved connection is recoverable, but
    # deleting it before topology persistence would discard retry information.
    _remove_saved_server_or_fail(registry, resolved.ip)

    err_console.print()
    ok("Uninstall complete.")
    err_console.print()
    err_console.print(f"  [dim]To redeploy: meridian deploy {resolved.ip}[/dim]")
    err_console.print()
