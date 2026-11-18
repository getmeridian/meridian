"""The destructive parts of the b12 handover rehearsal, on the lab's own single VM."""

from __future__ import annotations

import shlex
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from meridian.backup.keys import DEVICE_KEY_NAME
from meridian.backup.pull import SUFFIX
from meridian.cluster import ClusterConfig
from meridian.control.snapshot import read_header
from meridian.control_artifacts import service_package
from meridian.core.apply import CompiledApplyResult
from meridian.host_runtime import CONTROL_SERVICE_UNIT
from meridian.provision.control_service import INSTALLED_MARKER
from tests.realvm.handover_interrupt import INTERRUPTED
from tests.realvm.upgrade_support import VerificationError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.realvm.upgrade_verifier import UpgradeVerifier

SOURCE = "4.0.0b12"
# b12 was published 2026-11-04. Its unbounded dependency declarations now resolve
# a Typer it cannot start with. Recreate that day's installer state on both machines.
AS_OF = "2026-11-04T23:59:59Z"
DISPLAY = "Семья"
MARKER = "after-snapshot"


class HandoverDrill:
    def __init__(self, run: UpgradeVerifier) -> None:
        self.run = run
        self.snapshot = run.work / "before-handover.snapshot"
        self.epoch = 0
        self.stale_home = run.work / "stale-managing-computer"

    def require_lab_workspace(self) -> None:
        home = Path(self.run.env["MERIDIAN_HOME"])
        marker = home.parent / "fleet-id"
        if not (
            home.name == "home"
            and not home.is_symlink()
            and self.run.env.get("MERIDIAN_FLEET_ID")
            and marker.is_file()
            and marker.read_text().strip() == self.run.env["MERIDIAN_FLEET_ID"]
        ):
            raise VerificationError(
                "The b12 recovery drill requires its disposable orchestrator workspace.", exit_code=2
            )

    @contextmanager
    def published_resolver(self) -> Iterator[None]:
        """Refuse an existing config. Remove our dated tool resolver before the new install."""
        target = "/etc/uv/uv.toml"
        setting = shlex.quote(f'exclude-newer = "{AS_OF}"')
        self.run.execute(
            "Use the original release day's dependency resolver on the disposable VM",
            ["ssh", f"root@{self.run.ip}", f"set -Ce; mkdir -p /etc/uv; echo {setting} > {target}"],
        )
        try:
            yield
        finally:
            self.run.execute(
                "Remove the historical resolver before deploying the checkout",
                ["ssh", f"root@{self.run.ip}", f"grep -Fxq {setting} {target} && rm {target}"],
            )

    def save_snapshot(self) -> None:
        self.run.execute("Pull the pre-handover snapshot", [*self.run.previous, "backup", "pull"], old=True)
        home = Path(self.run.env["MERIDIAN_HOME"])
        snapshots = sorted((home / "snapshots").glob(f"*{SUFFIX}"), reverse=True)
        if not snapshots:
            raise VerificationError("The published release saved no snapshot.")
        shutil.copy2(snapshots[0], self.snapshot)
        self.epoch = read_header(self.snapshot.read_bytes()).fleet_epoch
        self.stale_home.mkdir(mode=0o700)
        for name in ("cluster.yml", "servers.json"):
            shutil.copy2(home / name, self.stale_home / name)
        self.run.pass_check("saved the pre-handover snapshot before any checkout apply")

    def apply_and_resume(self, people: list[str]) -> None:
        self.run.execute(
            "Interrupt the checkout apply at the local committed checkpoint",
            ["uv", "run", "python", "-m", "tests.realvm.handover_interrupt"],
            allowed=(INTERRUPTED,),
            certificates=True,
            timeout=1800,
        )
        cluster_path = Path(self.run.env["MERIDIAN_HOME"]) / "cluster.yml"
        adoption = ClusterConfig.load(cluster_path).people_adoption
        if adoption is None or adoption.state != "pending" or sorted(adoption.users) != people:
            raise VerificationError("Interruption left no pending adoption with the original people.")
        if self.run.people() != people or ClusterConfig.load(cluster_path).people_adoption is not None:
            raise VerificationError("A normal client list did not finish the pending handover.")
        self.run.pass_check("client list recovered the interrupted handover without another apply")

    def verify_checkout_install(self) -> None:
        artifact = service_package()
        marker = self.run.execute(
            "Read the installed control service wheel identity",
            ["ssh", f"root@{self.run.ip}", f"cat {INSTALLED_MARKER}"],
        )
        if not artifact.wheel or marker.stdout.strip() != artifact.identity:
            raise VerificationError("The control server is not running the wheel built from this checkout.")
        self.run.pass_check("the installed control service matches the checkout wheel SHA-256")

    def check_stale_copy(self) -> None:
        home = self.run.env["MERIDIAN_HOME"]
        self.run.env["MERIDIAN_HOME"] = str(self.stale_home)
        try:
            refused = self.run.execute(
                "Attempt an apply from the other computer's unchanged b12 files",
                [*self.run.current, "--json", "apply", "--yes"],
                allowed=(1, 2, 3, 4),
            )
        finally:
            self.run.env["MERIDIAN_HOME"] = home
        if "Another machine changed this fleet" not in (refused.stdout + refused.stderr):
            raise VerificationError("A stale managing computer did not return the expected fleet-fence refusal.")
        self.run.pass_check("a stale pre-handover managing computer was refused at the fleet fence")

    def unchanged_apply(self) -> None:
        applied = CompiledApplyResult.model_validate(self.run.json_command(["apply", "--yes"]))
        if not applied.actions or applied.changed or not applied.all_succeeded:
            raise VerificationError("A repeated reviewed apply did not report an unchanged, successful fleet.")
        self.run.pass_check("a second real apply reported no changes")

    def restore_snapshot(self, people: list[str]) -> None:
        # A surviving marker would expose a restore that just re-used the live post-upgrade database.
        self.run.execute(
            "Add a person who must disappear on restore", [*self.run.current, "client", "add", MARKER, "--yes"]
        )
        if self.run.people() != sorted([*people, MARKER]):
            raise VerificationError("The post-snapshot witness was not present before restore.")
        home = Path(self.run.env["MERIDIAN_HOME"])
        # Rebuild from only the saved snapshot and a device key. Use the same isolated HOME
        # after the switch so --keep and its SSH wrappers still point to the restored fleet.
        old = home.with_name(home.name + "-before-restore")
        home.rename(old)
        home.mkdir(mode=0o700)
        shutil.copy2(old / DEVICE_KEY_NAME, home / DEVICE_KEY_NAME)
        self.run.execute(
            "Stop the lab's control service for recovery",
            ["ssh", f"root@{self.run.ip}", f"systemctl stop {CONTROL_SERVICE_UNIT}"],
        )
        self.run.execute(
            "Restore the pre-handover snapshot onto the lab's original address",
            [*self.run.current, "restore", "--to", self.run.ip, "--snapshot", str(self.snapshot), "--yes"],
            certificates=True,
            timeout=1800,
        )
        restored = ClusterConfig.load(home / "cluster.yml")
        record = restored.control_restore
        if (
            record is None
            or record.step != "done"
            or record.fleet_epoch <= self.epoch
            or restored.people_adoption is not None
        ):
            raise VerificationError("The restore did not finish at a new epoch with the handover complete.")
        if self.run.people() != people:
            raise VerificationError("The pre-handover snapshot did not restore its original people.")
        self.run.pass_check("a fresh managing home restored the b12 snapshot on the same address at a newer epoch")
