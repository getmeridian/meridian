"""Remove server-side artifacts for resources that left the reviewed plan."""

from __future__ import annotations

import re
import shlex
from typing import Any

from meridian.cluster import ManagedResourceBinding
from meridian.reconciler.resources import ResourceReconcileError
from meridian.ssh import ServerConnection

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
