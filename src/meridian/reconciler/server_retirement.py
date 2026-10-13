"""Remove server-side artifacts for resources that left the reviewed plan."""

from __future__ import annotations

import re
import shlex
from typing import Any

from meridian.cluster import ManagedResourceBinding
from meridian.compiler.models import NodeRuntimePayload
from meridian.config import REMNAWAVE_NODE_DIR, REMNAWAVE_NODE_IMAGE
from meridian.reconciler.resources import ResourceReconcileError
from meridian.reconciler.server_render import realm_config_path, realm_service_name
from meridian.remnawave.versions import parse_image
from meridian.ssh import ServerConnection

_NODE_REPOSITORY = parse_image(REMNAWAVE_NODE_IMAGE).repository
_FIREWALL_MARKER = re.compile(r"meridian-v4-[0-9a-f]{16}")
_NGINX_ARTIFACT = re.compile(r"/etc/nginx/(stream\.d|sites-available)/meridian-v4-[0-9a-f]{16}\.conf")


def _connection(context: Any, binding: ManagedResourceBinding) -> ServerConnection:
    if not binding.server_ref:
        raise ResourceReconcileError(
            f"{binding.logical_id} was recorded before Meridian tracked its server; remove it manually.",
        )
    connection: ServerConnection = context.connection(binding.server_ref)
    return connection


class FirewallRetirement:
    """Delete UFW rules carrying this resource's Meridian comment."""

    context: Any

    def retire(self, binding: ManagedResourceBinding) -> None:
        if not _FIREWALL_MARKER.fullmatch(binding.remote_id):
            raise ResourceReconcileError(f"Unexpected firewall marker for {binding.logical_id}: {binding.remote_id!r}")
        conn = _connection(self.context, binding)
        # Highest number first so the remaining rule numbers stay valid.
        result = conn.run(
            "ufw status numbered 2>/dev/null"
            rf" | sed -n 's/^\[ *\([0-9][0-9]*\)\].*# {binding.remote_id}$/\1/p'"
            ' | sort -rn | while read -r rule; do ufw --force delete "$rule" >/dev/null || exit 1; done',
            timeout=30,
        )
        if result.returncode != 0:
            raise ResourceReconcileError(f"Could not delete firewall rule {binding.logical_id}.")


class NginxArtifactRetirement:
    """Delete this resource's nginx file (and its enabled link), then reload nginx."""

    context: Any

    def retire(self, binding: ManagedResourceBinding) -> None:
        path = binding.remote_id
        if not _NGINX_ARTIFACT.fullmatch(path):
            raise ResourceReconcileError(f"Unexpected nginx artifact path for {binding.logical_id}: {path!r}")
        enabled = path.replace("/sites-available/", "/sites-enabled/")
        conn = _connection(self.context, binding)
        result = conn.run(
            f"rm -f {shlex.quote(path)} {shlex.quote(enabled)} && nginx -t 2>&1 && systemctl reload-or-restart nginx",
            timeout=30,
        )
        if result.returncode != 0:
            raise ResourceReconcileError(f"nginx did not reload after removing {path}: {result.stdout.strip()[:200]}")


class RealmHopRetirement:
    """Stop and delete one hop's Realm unit and config; the Realm binary stays for other hops."""

    context: Any

    def retire(self, binding: ManagedResourceBinding) -> None:
        service = realm_service_name(binding.logical_id)
        if binding.remote_id != service:
            raise ResourceReconcileError(f"Unexpected Realm service for {binding.logical_id}: {binding.remote_id!r}")
        unit = f"/etc/systemd/system/{service}.service"
        conn = _connection(self.context, binding)
        result = conn.run(
            f"systemctl disable --now {shlex.quote(service)} >/dev/null 2>&1;"
            f" rm -f {shlex.quote(unit)} {shlex.quote(realm_config_path(binding.logical_id))}"
            f" && systemctl daemon-reload && systemctl reset-failed {shlex.quote(service)} >/dev/null 2>&1;"
            f" ! systemctl is-active --quiet {shlex.quote(service)}",
            timeout=60,
        )
        if result.returncode != 0:
            raise ResourceReconcileError(f"Realm service {service} is still running after removal.")


class NodeRuntimeRetirement:
    """Stop and delete the remnawave-node container, its Compose directory, and its image.

    The control plane lives in a separate Compose project, so it keeps running.
    When the reviewed plan still runs a node on the same server, the directory
    already belongs to that replacement and only the binding is forgotten.
    Docker itself stays: the baseline installed it and is never reverted, and
    the operator may run other containers.
    """

    context: Any

    def retire(self, binding: ManagedResourceBinding) -> None:
        if binding.remote_id != REMNAWAVE_NODE_DIR:
            raise ResourceReconcileError(f"Unexpected node runtime for {binding.logical_id}: {binding.remote_id!r}")
        conn = _connection(self.context, binding)
        if any(
            isinstance(resource.payload, NodeRuntimePayload) and resource.payload.server_ref == binding.server_ref
            for resource in self.context.plan.resources
        ):
            return
        compose = shlex.quote(f"{REMNAWAVE_NODE_DIR}/docker-compose.yml")
        result = conn.run(
            f"if [ -f {compose} ]; then docker compose -f {compose} down --remove-orphans --volumes || exit 1; fi;"
            " if docker inspect remnawave-node >/dev/null 2>&1;"
            " then docker rm -f remnawave-node >/dev/null || exit 1; fi;"
            f" rm -rf {shlex.quote(REMNAWAVE_NODE_DIR)};"
            # Images still used by another container refuse removal; that is fine.
            f" ids=$(docker images -q {shlex.quote(_NODE_REPOSITORY)} 2>/dev/null | sort -u);"
            ' if [ -n "$ids" ]; then docker image rm $ids >/dev/null 2>&1; fi; true',
            timeout=120,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:200]
            raise ResourceReconcileError(f"Could not remove the Remnawave node runtime: {detail}")
