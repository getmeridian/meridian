"""Hidden `meridian backup` sub-app and `meridian restore` (#95), mounted by `meridian.cli`.

Hidden while the control-service backend is: they stay out of the command catalog
and need no locale docs. Imports are deferred so `--help` stays fast.
"""

from __future__ import annotations

import typer

from meridian.cli_options import SshPort, User, Yes

backup_app = typer.Typer(
    help="Snapshots of the control service's state, device keys, and the recovery key", no_args_is_help=True
)


@backup_app.command("pull")
def backup_pull_cmd(
    name: str = typer.Option("", "--name", help="Name this device's key gets on first registration"),
) -> None:
    """Save the control service's newest encrypted snapshot in this context's snapshots/"""
    from meridian.commands.backup import run_pull

    run_pull(name=name)


@backup_app.command("recovery-key")
def backup_recovery_key_cmd(yes: Yes = False) -> None:
    """Make (or replace) the fleet's recovery key and show it once"""
    from meridian.commands.backup import run_recovery_key

    run_recovery_key(yes=yes)


@backup_app.command("devices")
def backup_devices_cmd() -> None:
    """List the device keys snapshots are sealed to"""
    from meridian.commands.backup import run_devices

    run_devices()


@backup_app.command("revoke")
def backup_revoke_cmd(
    device_id: str = typer.Argument(..., metavar="ID", help="Device key ID (backup devices)"),
) -> None:
    """Stop sealing snapshots to one device's key"""
    from meridian.commands.backup import run_revoke

    run_revoke(device_id)


def restore_cmd(
    to: str = typer.Option(..., "--to", metavar="SERVER", help="New control server: IP, user@IP[:port], or name"),
    snapshot: str = typer.Option("", "--snapshot", metavar="FILE", help="Snapshot to restore (default: newest)"),
    recovery_key: bool = typer.Option(
        False, "--recovery-key", help="Open the snapshot with the recovery key (asked for, or read from stdin)"
    ),
    force: bool = typer.Option(False, "--force", help="Restore even though the old control service still answers"),
    name: str = typer.Option("", "--name", help="Name for the new server"),
    user: User = "",
    ssh_port: SshPort = 0,
    yes: Yes = False,
) -> None:
    """Rebuild a lost or seized control server on another server from a snapshot"""
    from meridian.commands.restore import run

    run(
        to,
        snapshot=snapshot,
        recovery_key=recovery_key,
        force=force,
        yes=yes,
        user=user,
        ssh_port=ssh_port,
        name=name,
    )
