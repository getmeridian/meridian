"""Install the control service beside the panel and import the fleet's users into it.

The panel keeps 127.0.0.1:3000 and nginx's secret path until the cut-over, so
the service starts on `MIGRATION_SERVICE_PORT` with the fleet ID and admin
token the bootstrap will write again; the reviewed apply's bootstrap then moves
it to its own port. Until nginx routes to it, the deployer reaches it over SSH:
curl on the control server, the token in a 0600 file it deletes at once.
"""

from __future__ import annotations

import hashlib
import json
import shlex

from meridian.cluster import ClusterConfig
from meridian.control.models import ServiceConfig, UserImportBatch, UserImportResult
from meridian.control_artifacts import service_package
from meridian.host_runtime import CONTROL_SERVICE_CONFIG
from meridian.migrate import MigrationError
from meridian.provision.control_service import ControlServiceInstall, InstallControlService
from meridian.provision.steps import ProvisionContext
from meridian.ssh import ServerConnection

# Free on a Remnawave control server: the panel is 3000, the node API 3010, the subscription page 3020.
MIGRATION_SERVICE_PORT = 3030
_CURL_CONFIG = f"{CONTROL_SERVICE_CONFIG.rsplit('/', 1)[0]}/import.curl"


def install_beside_panel(conn: ServerConnection, cluster: ClusterConfig, token: str) -> None:
    """Install and start the service on the migration port, refusing a port something else holds."""
    port = MIGRATION_SERVICE_PORT
    busy = conn.run(f"ss -H -lnt 'sport = :{port}' 2>/dev/null", timeout=15)
    if busy.returncode == 0 and busy.stdout.strip():
        holder = conn.run(f"systemctl is-active ctld 2>/dev/null; cat {CONTROL_SERVICE_CONFIG} 2>/dev/null", timeout=15)
        if f'"listen_port": {port}' not in holder.stdout:
            raise MigrationError(
                f"Port {port} on the control server is in use, and Meridian needs it while it migrates.",
                hint=f"Free 127.0.0.1:{port} (ss -lntp 'sport = :{port}'), then run `meridian migrate` again.",
                category="user",
            )
    install = ControlServiceInstall(
        package=service_package(),
        config=ServiceConfig(
            fleet_id=cluster.fleet_id,
            admin_token_sha256=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            listen_port=port,
        ),
    )
    result = InstallControlService(install).run(conn, ProvisionContext(ip=conn.ip, user=conn.user))
    if result.status == "failed":
        raise MigrationError(
            f"Could not install the control service beside the panel: {result.detail}",
            hint="The panel still runs unchanged. Fix the cause, then run `meridian migrate` again.",
        )


def import_over_ssh(conn: ServerConnection, batch: UserImportBatch, token: str) -> UserImportResult:
    """POST the batch to the service's admin API from the control server itself."""
    curl_config = (
        f'url = "http://127.0.0.1:{MIGRATION_SERVICE_PORT}/api/import/users"\n'
        'request = "POST"\n'
        f'header = "Authorization: Bearer {token}"\n'
        'header = "Content-Type: application/json"\n'
    )
    written = conn.put_text(_CURL_CONFIG, curl_config, mode="600", sensitive=True, timeout=30)
    if written.returncode != 0:
        raise MigrationError(f"Could not prepare the user import: {written.stderr.strip()[:200]}")
    path = shlex.quote(_CURL_CONFIG)
    result = conn.run(
        f"curl -sS --max-time 120 -K {path} --data-binary @- -w '\\n%{{http_code}}'; rc=$?; rm -f {path}; exit $rc",
        timeout=180,
        input=batch.model_dump_json(),
        sensitive=True,
        operation_name="import users",
    )
    body, _, status = result.stdout.rstrip().rpartition("\n")
    if result.returncode != 0 or status != "200":
        raise MigrationError(
            f"The control service refused the user import: {_message(body) or result.stderr.strip()[:200]}",
            hint="Nothing was imported: it is all or nothing. Fix the cause, then run `meridian migrate` again.",
            category="user" if status in {"400", "409"} else "system",
        )
    return UserImportResult.model_validate_json(body)


def _message(body: str) -> str:
    try:
        return str(json.loads(body).get("error", {}).get("message", ""))[:300]
    except (ValueError, AttributeError):
        return body.strip()[:200]
