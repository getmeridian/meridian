"""Review recoverable owner data before stopping the last control service."""

from meridian.backup.pull import backup_client, pull_snapshot
from meridian.cluster import ClusterConfig
from meridian.console import fail, info, warn
from meridian.control.backup_models import BackupStatus
from meridian.core.errors import MeridianError


def status(cluster: ClusterConfig) -> BackupStatus | None:
    try:
        with backup_client(cluster, timeout=5) as client:
            return client.backup_status()
    except (MeridianError, ValueError, OSError):
        return None


def show_review(current: BackupStatus | None, *, discard: bool) -> None:
    warn("This is the last controller. Its live database, people, settings and subscriptions will be removed.")
    if current and current.last_saved_changes is not None and current.last_saved_at:
        info(
            f"Last confirmed off-server save: {current.last_saved_at}. Later changes: "
            f"{current.changes - current.last_saved_changes}."
        )
    elif current is not None:
        warn("No off-server backup has been confirmed.")
    else:
        warn("The last off-server save and number of newer changes could not be verified.")
    if discard:
        warn("You chose --discard-backup: teardown may permanently lose changes or the entire fleet.")
    else:
        info("A current, verified off-server copy is required. If one is missing, Meridian will try to save one.")


def confirmed_generation(cluster: ClusterConfig, current: BackupStatus | None) -> int:
    def fresh(item: BackupStatus | None) -> bool:
        return bool(item and item.has_deployer_state and item.last_saved_at and item.last_saved_changes == item.changes)

    if not fresh(current):
        try:
            pulled = pull_snapshot(cluster)
            if pulled.receipt == "confirmed":
                info(f"Verified recovery file saved to {pulled.path}. Keep the key separately.")
        except (MeridianError, ValueError, OSError):
            pass  # A separate managing device may have confirmed a copy in the meantime.
        current = status(cluster)
    if current is None or not fresh(current):
        fail(
            "Nothing was removed: no current off-server recovery copy could be confirmed.",
            hint="Run meridian backup pull from the computer that last applied the fleet. To permanently discard "
            "unbacked-up data, run teardown with --discard-backup.",
            hint_type="user",
        )
    return current.changes
