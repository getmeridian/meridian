"""Hidden `meridian backup` commands and the pull that follows every change to a service fleet (#95).

`pull_after_mutations()` is the one hook: the root callback installs it, every
`ControlClient` write that succeeds marks the run, and when the command is done
(even one that failed after changing something) the newest snapshot is pulled
into the context's `snapshots/`. A failed pull warns and never changes the
command's outcome.
"""

from __future__ import annotations

from collections.abc import Callable

import typer
from rich.markup import escape

from meridian import config
from meridian.backup.keys import device_key_path, rotate_recovery_key
from meridian.backup.pull import backup_client, pull_snapshot
from meridian.cluster import ClusterConfig
from meridian.commands._helpers import load_topology_cluster
from meridian.console import confirm, err_console, fail, has_tty, info, is_json_mode, is_quiet_mode, ok, warn
from meridian.control.client import add_mutation_listener
from meridian.control.snapshot import KeyPair
from meridian.core.errors import MeridianError

RECOVERY_HINT = "No recovery key yet: run `meridian backup recovery-key` and keep what it prints offline."


def pull_after_mutations(call_on_close: Callable[[Callable[[], None]], object]) -> None:
    """Record service writes during this run; pull one snapshot when the run closes."""
    written: set[str] = set()
    stop = add_mutation_listener(written.add)

    def finish() -> None:
        stop()
        if written:
            _pull_quietly(written)

    call_on_close(finish)


def _pull_quietly(urls: set[str]) -> None:
    try:
        cluster = ClusterConfig.load()
    except Exception:  # noqa: BLE001 - the command already reported whatever is wrong with local state
        return
    if not cluster.runs_control_service or cluster.panel.url.rstrip("/") not in urls:
        return
    try:
        pulled = pull_snapshot(cluster)
    except MeridianError as exc:
        warn(f"Could not back up the control service's state: {exc}")
        if exc.hint:
            err_console.print(f"    [dim]{escape(exc.hint)}[/dim]")
        return
    except Exception as exc:  # noqa: BLE001 - a backup must never fail the command it follows
        warn(f"Could not back up the control service's state: {type(exc).__name__}: {exc}")
        return
    if not is_quiet_mode():
        err_console.print(f"  [dim]Backed up the control service's state to {escape(_display(pulled.path))}[/dim]")
    if not pulled.has_recovery_key:
        warn(RECOVERY_HINT)


def run_pull(*, name: str = "") -> None:
    cluster = load_topology_cluster()
    try:
        pulled = pull_snapshot(cluster, name=name)
    except MeridianError as exc:
        fail(exc)
    header = pulled.header
    if pulled.registered:
        info(f"Registered this device's backup key ({device_key_path().name}) with the control service")
    if pulled.deposit_refused:
        warn("The control service holds newer fleet files than this machine's; this copy may be behind.")
    ok(f"Saved snapshot {_display(pulled.path)} (epoch {header.fleet_epoch}, revision {header.revision})")
    if not pulled.has_recovery_key:
        warn(RECOVERY_HINT)


def run_recovery_key(*, yes: bool = False) -> None:
    """Make (or replace) the fleet's recovery key and print it once."""
    cluster = load_topology_cluster()
    if is_json_mode():
        fail("A recovery key is shown only to a person", hint="Run without --json.", hint_type="user")
    try:
        # Its own write is a change like any other: the hook pulls a snapshot sealed to the new key.
        with backup_client(cluster, report_mutations=True) as client:
            current = client.recovery_key()
            if current.public_key and not yes:
                err_console.print()
                err_console.print(f"  This fleet has had a recovery key since {escape(current.set_at or '?')}.")
                err_console.print("  A new one replaces it for every snapshot from now on; snapshots you already")
                err_console.print("  hold still open with the old key.")
                if not confirm("Replace the recovery key?", default=False):
                    if not has_tty():
                        err_console.print("  [dim]No terminal to confirm on; pass --yes to replace it.[/dim]")
                    raise typer.Exit(1)
            pair = rotate_recovery_key(client)
    except MeridianError as exc:
        fail(exc)
    show_recovery_key(pair)


def offer_recovery_key(cluster: ClusterConfig) -> None:
    """At the end of a deploy: a service fleet without a recovery key gets one, shown once."""
    if not cluster.runs_control_service or is_json_mode() or is_quiet_mode():
        return
    try:
        with backup_client(cluster, report_mutations=True) as client:
            if client.recovery_key().public_key:
                return
            pair = rotate_recovery_key(client)
    except MeridianError as exc:
        warn(f"Could not create the recovery key: {exc}. Run `meridian backup recovery-key` later.")
        return
    show_recovery_key(pair)


def show_recovery_key(pair: KeyPair) -> None:
    print_ = err_console.print
    print_()
    print_("  [bold]Recovery key[/bold] [dim](shown once; Meridian keeps no copy)[/dim]")
    print_()
    print_(f"    [bold]{pair.secret}[/bold]", soft_wrap=True)
    print_()
    print_("  Write it down or print it, and keep it offline, away from this computer.")
    print_("  With it and any snapshot, `meridian restore --to NEW-SERVER --recovery-key` rebuilds")
    print_("  this fleet even if both this computer and the control server are gone.")
    print_(f"  [dim]Snapshots are saved in {escape(config.HOME_DISPLAY)}/snapshots after every change.[/dim]")
    print_()


def run_devices() -> None:
    cluster = load_topology_cluster()
    try:
        with backup_client(cluster) as client:
            devices = client.device_keys()
            recovery = client.recovery_key()
    except MeridianError as exc:
        fail(exc)
    err_console.print()
    for device in devices:
        state = f"revoked {device.revoked_at}" if device.revoked_at else f"since {device.created_at}"
        err_console.print(f"  [bold]{device.id}[/bold]  {escape(device.name)}  [dim]{state}[/dim]")
    if not devices:
        info("No device keys yet; `meridian backup pull` registers this one.")
    err_console.print(
        f"  [dim]Recovery key: {'set ' + (recovery.set_at or '') if recovery.public_key else 'none'}[/dim]"
    )
    err_console.print()


def run_revoke(device_id: str) -> None:
    cluster = load_topology_cluster()
    try:
        with backup_client(cluster, report_mutations=True) as client:
            record = client.revoke_device_key(device_id)
    except MeridianError as exc:
        fail(exc)
    ok(f"Revoked {record.name}'s backup key; later snapshots are not sealed to it")
    info("Snapshots it already holds still open with its key; revoke its API access separately.")


def _display(path: object) -> str:
    text = str(path)
    home = str(config.FLEET_HOME)
    return config.HOME_DISPLAY + text[len(home) :] if text.startswith(home) else text
