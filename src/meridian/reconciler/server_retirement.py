"""Remove server-side artifacts for resources that left the reviewed plan."""

from __future__ import annotations

import re
import shlex
from typing import Any

from meridian.cluster import ManagedResourceBinding
from meridian.compiler.models import (
    IP_CERTIFICATE_DIRECTORY,
    CertificatePayload,
    ControlPlaneRuntimePayload,
    NodeRuntimePayload,
    certificate_directory,
)
from meridian.config import REMNAWAVE_NODE_IMAGE
from meridian.host_runtime import LEGACY_NODE_DIR, NODE, NODE_DIR
from meridian.node_deploy import delete_node_api_rules
from meridian.provision.runtime_migration import remove_legacy_node_command
from meridian.reconciler.resources import ResourceReconcileError
from meridian.reconciler.server_render import (
    legacy_firewall_marker,
    legacy_realm_config_path,
    legacy_realm_service_name,
    realm_config_path,
    realm_service_name,
    unit_path,
)
from meridian.remnawave.versions import parse_image
from meridian.ssh import ServerConnection

_NODE_REPOSITORY = parse_image(REMNAWAVE_NODE_IMAGE).repository
_FIREWALL_MARKER = re.compile(r"(?:fw|meridian-v4)-[0-9a-f]{16}")
_CERTIFICATE = re.compile(r"/etc/ssl/meridian(?:/certs/[0-9a-f]{16})?/fullchain\.pem")
ACME_CHALLENGE_PATH = "/etc/nginx/sites-available/meridian-v4-acme.conf"
_NGINX_ARTIFACT = re.compile(
    r"/etc/nginx/(stream\.d|sites-available|meridian-v4-locations)/meridian-v4-[0-9a-f]{16}\.conf"
)


def _connection(context: Any, binding: ManagedResourceBinding) -> ServerConnection:
    if not binding.server_ref:
        raise ResourceReconcileError(
            f"{binding.logical_id} was recorded before Meridian tracked its server; remove it manually.",
        )
    connection: ServerConnection = context.connection(binding.server_ref)
    return connection


def delete_rules_by_comment(conn: ServerConnection, marker: str) -> bool:
    """Delete every UFW rule whose comment is exactly ``marker``; True when the deletion completed."""
    if not _FIREWALL_MARKER.fullmatch(marker):
        raise ResourceReconcileError(f"Refusing to delete firewall rules by unexpected comment {marker!r}.")
    # Highest number first so the remaining rule numbers stay valid.
    result = conn.run(
        "ufw status numbered 2>/dev/null"
        rf" | sed -n 's/^\[ *\([0-9][0-9]*\)\].*# {marker}$/\1/p'"
        ' | sort -rn | while read -r rule; do ufw --force delete "$rule" >/dev/null || exit 1; done',
        timeout=30,
    )
    return result.returncode == 0


class FirewallRetirement:
    """Delete UFW rules carrying this resource's comment, current or pre-rename."""

    context: Any

    def retire(self, binding: ManagedResourceBinding) -> None:
        if not _FIREWALL_MARKER.fullmatch(binding.remote_id):
            raise ResourceReconcileError(f"Unexpected firewall marker for {binding.logical_id}: {binding.remote_id!r}")
        conn = _connection(self.context, binding)
        markers = dict.fromkeys((binding.remote_id, legacy_firewall_marker(binding.logical_id)))
        if not all(delete_rules_by_comment(conn, marker) for marker in markers):
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


class CertificateRetirement:
    """Stop renewing a certificate no Host uses any more and delete its files.

    acme.sh records where it installs each certificate, so the renewal entry is found
    from the binding's ``fullchain.pem`` path, even for a hashed domain directory. The
    panel shares the server's IP certificate, so nothing is touched while a control
    plane runs there or another certificate in the plan has the same files. The ACME
    challenge listener goes once no certificate is left on the server.
    """

    context: Any

    def retire(self, binding: ManagedResourceBinding) -> None:
        fullchain = binding.remote_id
        if not _CERTIFICATE.fullmatch(fullchain):
            raise ResourceReconcileError(f"Unexpected certificate path for {binding.logical_id}: {fullchain!r}")
        conn = _connection(self.context, binding)
        kept = [
            resource.payload
            for resource in self.context.plan.resources
            if isinstance(resource.payload, CertificatePayload | ControlPlaneRuntimePayload)
            and resource.payload.server_ref == binding.server_ref
        ]
        if any(isinstance(payload, ControlPlaneRuntimePayload) for payload in kept):
            return
        directory = fullchain.rsplit("/", 1)[0]
        if any(
            isinstance(payload, CertificatePayload) and certificate_directory(payload.hostname) == directory
            for payload in kept
        ):
            return
        record = shlex.quote(f"Le_RealFullChainPath='{fullchain}'")
        files = f"{shlex.quote(fullchain)} {shlex.quote(directory + '/key.pem')}"
        command = (
            f"for conf in $(grep -l -F {record} /root/.acme.sh/*/*.conf 2>/dev/null);"
            ' do rm -rf "$(dirname "$conf")" || exit 1; done;'
            f" rm -f {files}"
        )
        if directory != IP_CERTIFICATE_DIRECTORY:
            command += f" && rmdir --ignore-fail-on-non-empty {shlex.quote(directory)} 2>/dev/null; true"
        if not kept:
            enabled = ACME_CHALLENGE_PATH.replace("/sites-available/", "/sites-enabled/")
            command = (
                f"({command}) && rm -f {shlex.quote(ACME_CHALLENGE_PATH)} {shlex.quote(enabled)}"
                " && { ! systemctl is-active --quiet nginx || { nginx -t 2>&1 && systemctl reload nginx; }; }"
            )
        result = conn.run(command, timeout=60)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:200]
            raise ResourceReconcileError(f"Could not remove certificate {fullchain}: {detail}")


class RealmHopRetirement:
    """Stop and delete one hop's forwarder unit and config under both names; the binary stays for other hops."""

    context: Any

    def retire(self, binding: ManagedResourceBinding) -> None:
        current, legacy = realm_service_name(binding.logical_id), legacy_realm_service_name(binding.logical_id)
        if binding.remote_id not in {current, legacy}:
            raise ResourceReconcileError(f"Unexpected Realm service for {binding.logical_id}: {binding.remote_id!r}")
        conn = _connection(self.context, binding)
        services = f"{shlex.quote(current)} {shlex.quote(legacy)}"
        paths = (
            unit_path(current),
            unit_path(legacy),
            realm_config_path(binding.logical_id),
            legacy_realm_config_path(binding.logical_id),
        )
        # One unit per systemctl call: a multi-unit `disable --now` aborts entirely when
        # either unit is missing (a fresh relay has no legacy unit) and stops nothing.
        # Stopping again after daemon-reload catches a unit whose file is already gone.
        stop_each = (
            f"for unit in {services};"
            ' do systemctl disable --now "$unit" >/dev/null 2>&1; systemctl stop "$unit" >/dev/null 2>&1; done;'
        )
        result = conn.run(
            f"{stop_each}"
            f" rm -f {' '.join(shlex.quote(path) for path in paths)}"
            f" && systemctl daemon-reload && {stop_each}"
            f" systemctl reset-failed {services} >/dev/null 2>&1;"
            f" ! systemctl is-active --quiet {shlex.quote(current)}"
            f" && ! systemctl is-active --quiet {shlex.quote(legacy)}",
            timeout=60,
        )
        if result.returncode != 0:
            raise ResourceReconcileError(f"Realm service {binding.remote_id} is still running after removal.")


class NodeRuntimeRetirement:
    """Stop and delete the node container, its Compose directory, and its image, under both names.

    The control plane lives in a separate Compose project, so it keeps running.
    When the reviewed plan still runs a node on the same server, the directory
    already belongs to that replacement and only the binding is forgotten.
    Docker itself stays: the baseline installed it and is never reverted, and
    the operator may run other containers.
    """

    context: Any

    def retire(self, binding: ManagedResourceBinding) -> None:
        if binding.remote_id not in {NODE_DIR, LEGACY_NODE_DIR}:
            raise ResourceReconcileError(f"Unexpected node runtime for {binding.logical_id}: {binding.remote_id!r}")
        conn = _connection(self.context, binding)
        if any(
            isinstance(resource.payload, NodeRuntimePayload) and resource.payload.server_ref == binding.server_ref
            for resource in self.context.plan.resources
        ):
            return
        compose = shlex.quote(f"{NODE_DIR}/docker-compose.yml")
        name = shlex.quote(NODE.name)
        result = conn.run(
            f"if [ -f {compose} ]; then docker compose -f {compose} down --remove-orphans --volumes || exit 1; fi;"
            f" if docker inspect --type container {name} >/dev/null 2>&1;"
            f" then docker rm -f {name} >/dev/null || exit 1; fi;"
            f" rm -rf {shlex.quote(NODE_DIR)}; {remove_legacy_node_command()} || exit 1;"
            # Images still used by another container refuse removal; that is fine.
            f" ids=$(docker images -q {shlex.quote(_NODE_REPOSITORY)} 2>/dev/null | sort -u);"
            ' if [ -n "$ids" ]; then docker image rm $ids >/dev/null 2>&1; fi; true',
            timeout=120,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:200]
            raise ResourceReconcileError(f"Could not remove the Remnawave node runtime: {detail}")
        if not delete_node_api_rules(conn):
            raise ResourceReconcileError("Could not remove the node API firewall rule.")
