"""Repair a stopped or broken control service without an apply (#124).

Every apply needs the service, since its fence lives there and an unreachable
fence is never bypassed. So when `ctld` is stopped, or its installed package is
broken, `meridian check --repair` reinstalls the same `meridian-vpn` under
`/opt/ctld` with the server's own configuration, rewrites the unit, restarts it,
and waits until it answers. Nothing an apply owns changes: topology, people,
node documents, the fence, and revisions stay in the database as they are.

It refuses a service that answers (an apply is the way), a server without its
configuration or database (`meridian restore` is), and a recorded install of
another version than this CLI's: reinstalling would change the version without
the reviewed apply that goes with it.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from packaging.version import InvalidVersion, Version
from pydantic import ValidationError

from meridian.control.models import ServiceConfig
from meridian.control_artifacts import ServicePackage, package_version
from meridian.core.errors import InfrastructureEvidenceUnavailableError, ProvisioningError, release_command
from meridian.core.version_skew import service_skew
from meridian.host_runtime import CONTROL_SERVICE_CONFIG
from meridian.provision.control_service import INSTALLED_MARKER, ControlServiceInstall, InstallControlService
from meridian.provision.steps import ProvisionContext, StepResult
from meridian.ssh import ServerConnection

REPAIR_COMMAND = "check --repair"
# The first release with the repair; an older one can't be asked to repair itself.
FIRST_REPAIR_RELEASE = Version("4.0.0b17")


@dataclass(frozen=True)
class RepairReview:
    """What a repair reinstalls on the control server, read before anything changes."""

    config: ServiceConfig
    package: ServicePackage
    recorded: str  # The server's install record; "" when it has none.

    @property
    def database(self) -> str:
        return f"{self.config.state_dir.rstrip('/')}/control.db"


def review_repair(conn: ServerConnection, package: ServicePackage, *, fleet_id: str, host: str) -> RepairReview:
    """Read the control server and refuse unless a repair is both needed and safe."""
    config = _read_config(conn, host)
    if fleet_id and config.fleet_id != fleet_id:
        raise ProvisioningError(
            "That server's control service belongs to another fleet.",
            hint="Run the repair from the context that manages that fleet (meridian context list).",
            category="user",
        )
    if _answers(conn, config.listen_port):
        raise ProvisioningError(
            "The control service is running; use `meridian apply`.",
            hint="A repair is for a control service that is stopped or broken. "
            "`meridian check` shows what else is wrong.",
            category="user",
        )
    review = RepairReview(config=config, package=package, recorded=_read_record(conn))
    _require_database(conn, review.database, host)
    _require_same_version(review, host)
    return review


def repair(conn: ServerConnection, review: RepairReview) -> StepResult:
    """Reinstall the package with the same configuration and unit, restart, and wait until it answers."""
    install = ControlServiceInstall(package=review.package, config=review.config)
    return InstallControlService(install, reinstall=True).run(conn, ProvisionContext(ip=conn.ip, user=conn.user))


def _read_config(conn: ServerConnection, host: str) -> ServiceConfig:
    path = shlex.quote(CONTROL_SERVICE_CONFIG)
    read = conn.run(f"if [ -e {path} ]; then cat {path}; fi", timeout=15, sensitive=True)
    if read.returncode != 0:
        detail = read.stderr.strip()[:200] or f"exit {read.returncode}"
        raise InfrastructureEvidenceUnavailableError(
            f"Could not read {CONTROL_SERVICE_CONFIG} on the control server: {detail}",
            hint="Check SSH to the control server, then run the repair again.",
        )
    if not read.stdout.strip():
        raise _rebuild(f"The control server has no {CONTROL_SERVICE_CONFIG}, so a repair has nothing to keep.", host)
    try:
        return ServiceConfig.model_validate_json(read.stdout)
    except ValidationError:
        raise _rebuild(f"{CONTROL_SERVICE_CONFIG} on the control server is unreadable.", host) from None


def _answers(conn: ServerConnection, port: int) -> bool:
    """Whether the service answers on loopback: its health endpoint refuses a request without a token."""
    probe = conn.run(
        f"curl -s --max-time 5 -o /dev/null -w '%{{http_code}}' http://127.0.0.1:{port}/api/health", timeout=15
    )
    return probe.stdout.strip() == "401"


def _read_record(conn: ServerConnection) -> str:
    read = conn.run(f"if [ -e {INSTALLED_MARKER} ]; then cat {INSTALLED_MARKER}; fi", timeout=15)
    if read.returncode != 0:
        detail = (read.stderr or read.stdout).strip()[:200] or f"exit {read.returncode}"
        raise InfrastructureEvidenceUnavailableError(
            f"Could not read which service package is installed: {detail}",
            hint="Check SSH to the control server, then run the repair again.",
        )
    return read.stdout.strip()


def _require_database(conn: ServerConnection, database: str, host: str) -> None:
    found = conn.run(f"test -s {shlex.quote(database)}", timeout=15)
    if found.returncode == 1:
        raise _rebuild(f"The control server has no database at {database}, so a repair has nothing to keep.", host)
    if found.returncode != 0:
        raise InfrastructureEvidenceUnavailableError(
            f"Could not check the control service's database: {found.stderr.strip()[:200] or 'no answer'}",
            hint="Check SSH to the control server, then run the repair again.",
        )


def _require_same_version(review: RepairReview, host: str) -> None:
    """A release repairs only its own version; a checkout may put its wheel over any version but a newer one."""
    installed = package_version(review.recorded)
    wanted = review.package.version
    if not installed or installed == wanted:
        return
    if review.package.wheel and service_skew(wanted, installed) != "service_newer":
        return
    if _has_repair(installed):
        hint = "A repair reinstalls the same version. Run it with that release: " + release_command(
            installed, REPAIR_COMMAND
        )
    else:
        hint = (
            f"A repair reinstalls the same version, and Meridian {installed} has none. On the control server, "
            f"run `systemctl restart ctld`; if it still does not answer, rebuild it: meridian restore --to {host}"
        )
    raise ProvisioningError(
        f"The control server has Meridian {installed} installed, and this is Meridian {wanted}.",
        hint=hint,
        category="user",
    )


def _has_repair(version: str) -> bool:
    try:
        return Version(version) >= FIRST_REPAIR_RELEASE
    except InvalidVersion:
        return False


def _rebuild(message: str, host: str) -> ProvisioningError:
    return ProvisioningError(
        message,
        hint=f"Rebuild the control service from a snapshot: meridian restore --to {host}",
        category="user",
    )
