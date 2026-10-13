"""Refuse target servers Meridian 4 must not touch, before any mutation.

V4 takes port 443 with its own nginx stream file and stops HAProxy/Caddy left by
older releases, so applying onto such a server silently cuts off every user of
the old deployment. Provisioning is apt/dpkg/ufw based, so a server without apt
(CentOS, Fedora, Alpine) is refused instead of failing halfway through.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping

from meridian.core.errors import InfrastructureEvidenceUnavailableError, ProvisioningError
from meridian.reconciler.server_evidence import require_successful_evidence
from meridian.ssh import ServerConnection

_DONE = "meridian-legacy-check-done"
_OS_PREFIX = "unsupported-os:"

# Each probe prints its label only when the artifact exists; the trailing
# sentinel proves the whole script ran rather than stopping early.
LEGACY_DEPLOYMENT_CHECK = (
    "if ! command -v apt-get >/dev/null 2>&1 || ! command -v dpkg-query >/dev/null 2>&1; then "
    "printf '%s%s\\n' "
    f"{shlex.quote(_OS_PREFIX)} "
    '"$(. /etc/os-release 2>/dev/null; printf %s "${PRETTY_NAME:-}")"; fi; '
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
    return _legacy(_target_lines(connection))


def refuse_legacy_deployments(connections: Mapping[str, ServerConnection], addresses: Mapping[str, str]) -> None:
    """Stop before apply when any target server lacks apt or still runs an older Meridian."""
    for server_ref in sorted(connections):
        lines = _target_lines(connections[server_ref])
        address = addresses.get(server_ref, server_ref)
        unsupported = next((line.removeprefix(_OS_PREFIX) for line in lines if line.startswith(_OS_PREFIX)), None)
        if unsupported is not None:
            raise ProvisioningError(
                f"{address} runs {unsupported or 'an OS without apt'}; Meridian supports Ubuntu and Debian only",
                hint="Reinstall the server with Ubuntu 24.04 LTS or Debian 13, then retry.",
                category="user",
            )
        found = _legacy(lines)
        if not found:
            continue
        raise ProvisioningError(
            f"{address} runs {found}; Meridian 4 would take over port 443 and cut off its users",
            hint=(
                "Use a separate server for Meridian 4. To replace the old deployment instead, "
                f"remove it first with `meridian teardown {address}` (its users lose access), then retry."
            ),
            category="user",
        )


def _target_lines(connection: ServerConnection) -> list[str]:
    result = connection.run(LEGACY_DEPLOYMENT_CHECK, timeout=30, operation_name="check for Meridian 3.x")
    require_successful_evidence("legacy-deployment", result, "older Meridian deployment check")
    lines = [line.strip() for line in result.stdout.splitlines()]
    if _DONE not in lines:
        raise InfrastructureEvidenceUnavailableError(
            "Could not tell whether the server runs an older Meridian: the check did not finish.",
            hint="Restore SSH access to the server, then retry.",
        )
    return lines


def _legacy(lines: list[str]) -> str:
    found = [_DESCRIPTIONS[line] for line in lines if line in _DESCRIPTIONS]
    return found[0] if found else ""
