"""Refuse to deploy over a live Meridian 3.x (or older) installation.

V4 takes port 443 with its own nginx stream file and stops HAProxy/Caddy left by
older releases, so applying onto such a server silently cuts off every user of
the old deployment. The check runs before any local or remote mutation.
"""

from __future__ import annotations

from collections.abc import Mapping

from meridian.core.errors import InfrastructureEvidenceUnavailableError, ProvisioningError
from meridian.reconciler.server_evidence import require_successful_evidence
from meridian.ssh import ServerConnection

_DONE = "meridian-legacy-check-done"

# Each probe prints its label only when the artifact exists; the trailing
# sentinel proves the whole script ran rather than stopping early.
LEGACY_DEPLOYMENT_CHECK = (
    "[ -e /opt/3x-ui ] && echo 3x-ui; "
    "docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx 3x-ui && echo 3x-ui-container; "
    "[ -e /etc/systemd/system/meridian-relay.service ] && echo 3x-relay; "
    "grep -qs '^# HAProxy SNI Router' /etc/haproxy/haproxy.cfg && echo haproxy; "
    "[ -e /etc/caddy/conf.d/meridian.caddy ] && echo caddy; "
    f"echo {_DONE}"
)

_DESCRIPTIONS = {
    "3x-ui": "a Meridian 3.x proxy (3x-ui)",
    "3x-ui-container": "a Meridian 3.x proxy (3x-ui)",
    "3x-relay": "a Meridian 3.x relay",
    "haproxy": "a Meridian 2.x proxy (HAProxy)",
    "caddy": "a Meridian 2.x proxy (Caddy)",
}


def legacy_deployment(connection: ServerConnection) -> str:
    """Describe the older Meridian deployment on a server, or return an empty string."""
    result = connection.run(LEGACY_DEPLOYMENT_CHECK, timeout=30, operation_name="check for Meridian 3.x")
    require_successful_evidence("legacy-deployment", result, "older Meridian deployment check")
    lines = result.stdout.split()
    if _DONE not in lines:
        raise InfrastructureEvidenceUnavailableError(
            "Could not tell whether the server runs an older Meridian: the check did not finish.",
            hint="Restore SSH access to the server, then retry.",
        )
    found = [_DESCRIPTIONS[line] for line in lines if line in _DESCRIPTIONS]
    return found[0] if found else ""


def refuse_legacy_deployments(connections: Mapping[str, ServerConnection], addresses: Mapping[str, str]) -> None:
    """Stop before apply when any target server still runs an older Meridian."""
    for server_ref in sorted(connections):
        found = legacy_deployment(connections[server_ref])
        if not found:
            continue
        address = addresses.get(server_ref, server_ref)
        raise ProvisioningError(
            f"{address} runs {found}; Meridian 4 would take over port 443 and cut off its users",
            hint=(
                "Use a separate server for Meridian 4. To replace the old deployment instead, "
                f"remove it first with `meridian teardown {address}` (its users lose access), then retry."
            ),
            category="user",
        )
