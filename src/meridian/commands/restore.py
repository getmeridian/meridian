"""`meridian restore --to NEW-SERVER` (hidden while the control service is): rebuild the control server (#95).

For a control server that died or was seized: the newest snapshot in this context
(or `--snapshot FILE`), opened with this device's key or `--recovery-key`, becomes
the control service on NEW-SERVER at the next fleet epoch, and the normal reviewed
apply re-enrolls every agent. On a machine with no fleet files, the snapshot's own
copy of `cluster.yml` and `servers.json` is restored into the context first.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

import typer
from rich.markup import escape

from meridian import config
from meridian.backup import BackupError
from meridian.backup.keys import load_device_key, write_private
from meridian.backup.pull import saved_snapshots
from meridian.backup.restore import ControlRestorer, RestoreOutcome, RestoreReview, RestoreSource
from meridian.cluster import ClusterConfig
from meridian.commands._helpers import apply_reviewed_intent, load_cluster, persist_reviewed_apply
from meridian.console import confirm, err_console, fail, has_tty, info, ok, warn
from meridian.control.restore import read_deployer_state
from meridian.control.snapshot import Snapshot, decrypt_snapshot
from meridian.core.errors import MeridianError
from meridian.core.topology import SetupIntent
from meridian.servers import ServerRegistry


def run(
    to: str,
    *,
    snapshot: str = "",
    recovery_key: bool = False,
    force: bool = False,
    yes: bool = False,
    user: str = "",
    ssh_port: int = 0,
    name: str = "",
) -> None:
    from meridian.commands.resolve import resolve_server
    from meridian.commands.server import register_server

    cluster = load_cluster()
    resuming = cluster.control_restore is not None and cluster.control_restore.step != "done"
    if resuming and cluster.control_restore is not None and cluster.control_restore.reached("staged"):
        source = RestoreSource(Path(cluster.control_restore.snapshot), None)
    else:
        source = _open_snapshot(snapshot, recovery_key=recovery_key)
        cluster = _fleet_state(cluster, source.snapshot)
    registry = ServerRegistry(config.SERVER_PROFILES_FILE)
    target = register_server(registry, resolve_server(registry, explicit_ip=to, user=user, port=ssh_port), name=name)
    restorer = ControlRestorer(cluster, registry, persist=persist_reviewed_apply, on_step=info)
    try:
        review = restorer.review(target, source, force=force)
    except MeridianError as exc:
        fail(exc)
    _render(review, registry)
    if not yes and not confirm("Restore the control service now?", default=False):
        if not has_tty():
            err_console.print("  [dim]No terminal to confirm on; pass --yes to restore without asking.[/dim]")
        raise typer.Exit(1)

    def apply(state: ClusterConfig, intent: SetupIntent) -> None:
        apply_reviewed_intent(
            state,
            registry,
            intent,
            failure="The restored fleet did not finish applying",
            hint="Run the same `meridian restore` again; it continues from here.",
        )

    try:
        outcome = restorer.run(review, apply)
    except MeridianError as exc:
        fail(exc)
    _done(outcome)


def _open_snapshot(explicit: str, *, recovery_key: bool) -> RestoreSource:
    if explicit:
        path = Path(explicit).expanduser()
    else:
        saved = saved_snapshots()
        if not saved:
            fail(
                f"No snapshots in {config.HOME_DISPLAY}/snapshots",
                hint="Name one with --snapshot FILE (a copy from another machine or an offsite store).",
                hint_type="user",
            )
        path = saved[0]
    try:
        blob = path.read_bytes()
    except OSError as exc:
        fail(f"Cannot read {path}: {exc}", hint_type="user")
    if recovery_key:
        keys = [_read_recovery_key()]
    else:
        try:
            device = load_device_key()
        except MeridianError as exc:
            fail(exc)
        if device is None:
            fail(
                "This context has no device key to open the snapshot with",
                hint="Use the fleet's recovery key: add --recovery-key.",
                hint_type="user",
            )
        keys = [device.secret]
    try:
        return RestoreSource(path, decrypt_snapshot(blob, keys))
    except MeridianError as exc:
        fail(exc)


def _read_recovery_key() -> str:
    """The recovery key from a hidden prompt, or from stdin without a terminal; never from argv."""
    if sys.stdin.isatty():
        key = getpass.getpass("  Recovery key (AGE-SECRET-KEY-1...): ")
    else:
        key = sys.stdin.readline()
    if not key.strip():
        fail("No recovery key given", hint="Paste it at the prompt, or pipe it on stdin.", hint_type="user")
    return key


def _fleet_state(cluster: ClusterConfig, snapshot: Snapshot | None) -> ClusterConfig:
    """This context's fleet files, or the snapshot's copy of them on a machine that has none."""
    if snapshot is None:
        return cluster
    try:
        deposited = read_deployer_state(snapshot.database)
    except MeridianError as exc:
        fail(exc)
    if cluster.topology_intent is not None:
        if deposited is not None and cluster.fleet_id == snapshot.header.fleet_id:
            if deposited.generation > cluster.active_generation:
                fail(
                    "This machine's copy of the fleet is behind the snapshot's",
                    hint=f"The snapshot holds generation {deposited.generation}, this copy "
                    f"{cluster.active_generation}. Restore into a new context, which takes the snapshot's copy: "
                    "meridian restore --to NEW-SERVER --context NAME",
                    hint_type="user",
                )
        return cluster
    if deposited is None:
        fail(
            "The snapshot carries no fleet files, and this context has none",
            hint="Copy cluster.yml and servers.json from a machine that managed the fleet, then retry.",
            hint_type="user",
        )
    if config.SERVER_PROFILES_FILE.exists() and config.SERVER_PROFILES_FILE.read_text(encoding="utf-8").strip():
        fail(
            f"{config.HOME_DISPLAY} holds saved servers but no fleet",
            hint="Restore into an empty context: meridian restore --to NEW-SERVER --context NAME",
            hint_type="user",
        )
    write_private(config.CLUSTER_CONFIG, deposited.cluster_yml)
    write_private(config.SERVER_PROFILES_FILE, deposited.servers_json)
    info(f"Restored this fleet's cluster.yml and servers.json from the snapshot into {config.HOME_DISPLAY}")
    restored = load_cluster()
    if restored.fleet_id != snapshot.header.fleet_id:
        raise BackupError("The snapshot's fleet files name another fleet.", category="bug")
    return restored


def _render(review: RestoreReview, registry: ServerRegistry) -> None:
    def label(server_ref: str) -> str:
        entry = registry.find(server_ref)
        return escape(entry.name or entry.host) if entry is not None else escape(server_ref)

    print_ = err_console.print
    target = escape(review.target.name or review.target.host)
    print_()
    print_(f"  [bold]Restore this fleet's control service onto {target}[/bold]")
    if review.resume_from is not None:
        print_(f"  [dim]Continuing after step: {escape(review.resume_from)}[/dim]")
    header = review.header
    if header is not None:
        print_(
            f"  From {escape(review.source.path.name)}: taken {escape(header.created_at)}, revision {header.revision}; "
            f"the fleet moves from epoch {header.fleet_epoch} to {header.fleet_epoch + 1}"
        )
    old = label(review.from_server_ref)
    if review.from_server_ref != review.target.id:
        moved = f" and exits {escape(', '.join(review.moved_exits))}" if review.moved_exits else ""
        print_(f"  Moves the control plane{moved} from {old} to {target}.")
        if review.old_reachable:
            print_(f"  {old} still answers over SSH: Meridian removes what it deployed there.")
        else:
            print_(
                f"  {old} does not answer over SSH: its resources are forgotten. Delete or wipe it at your provider."
            )
    print_("  Every agent enrolls again under a new fleet CA; once an agent sees the new epoch, it refuses")
    print_("  anything the old control server still signs.")
    print_("  A new admin token replaces the old one: other machines that manage this fleet need a fresh copy")
    print_(f"  of {escape(config.HOME_DISPLAY)} afterwards.")
    if review.links_change:
        print_(
            f"  [bold yellow]Every subscription URL changes[/bold yellow]: links name {escape(review.from_address)}, "
            f"and will name {escape(review.target.host)}."
        )
        print_("  Each client's new link is printed at the end; send it to them. Their apps keep failing on the old")
        print_("  link until they import the new one.")
    else:
        print_("  Subscription URLs stay the same once DNS points at the new server.")
    if review.dns_names:
        names = escape(", ".join(review.dns_names))
        print_(f"  [bold]Point DNS[/bold] for {names} at {escape(review.target.host)} (before or right after).")
    for binding in review.setup.retiring:
        where = label(binding.server_ref) if binding.server_ref else "service"
        print_(f"    [red]remove[/red] {escape(binding.logical_id)} [dim]({where})[/dim]")
    print_()


def _done(outcome: RestoreOutcome) -> None:
    ok(f"Restored the control service at fleet epoch {outcome.fleet_epoch}")
    if outcome.links_change:
        warn("Every subscription URL changed. Send each client their new link:")
    elif outcome.links:
        info("Subscription URLs are unchanged; for reference:")
    for links in outcome.links:
        if links.error:
            err_console.print(f"    [warn]{escape(links.name)}[/warn]: {escape(links.error)}")
            continue
        err_console.print(f"    [bold]{escape(links.name)}[/bold]  {escape(links.subscription_url)}")
        if links.page_url:
            err_console.print(f"      [dim]page: {escape(links.page_url)}[/dim]")
