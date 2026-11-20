"""Uninstall provisioning steps.

Removes proxy components but leaves Docker engine and system packages intact.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from dataclasses import dataclass
from importlib.resources import files

from meridian.host_runtime import (
    LEGACY_PANEL_DIR,
    LEGACY_PANEL_NETWORK,
    LEGACY_PANEL_SOCKET_VOLUME,
    PANEL_DIR,
    PANEL_PROJECT,
    PANEL_STOP_ORDER,
    certificate_directory,
)
from meridian.provision.steps import ProvisionContext, StepResult
from meridian.reconciler.server_render import firewall_marker, legacy_firewall_marker
from meridian.ssh import ServerConnection


def _compose_removal(*directories: str) -> list[str]:
    """Bring each Compose project down (images and volumes too), then delete its directory."""
    commands = []
    for directory in directories:
        quoted = shlex.quote(directory)
        commands += [f"cd {quoted} && docker compose down --rmi all -v 2>/dev/null; true", f"rm -rf {quoted}"]
    return commands


def _remove_containers(*names: str) -> str:
    """Remove containers a missing or edited compose file no longer names."""
    return f"docker rm -f {' '.join(shlex.quote(name) for name in names)} >/dev/null 2>&1; true"


def panel_stack_removal() -> list[str]:
    """Remove the Remnawave panel stack under its neutral and pre-rename names: Compose projects,
    containers, images, the data directories (PostgreSQL's ``data/``), and leftover volumes and networks.

    Migration runs this only after verifying that the new control service has taken over.
    """
    return [
        *_compose_removal(PANEL_DIR, LEGACY_PANEL_DIR),
        _remove_containers(*(name for container in PANEL_STOP_ORDER for name in (container.name, container.legacy))),
        # Docker objects that may remain after compose down (pre-rename projects took the directory name)
        (
            f"docker volume rm {PANEL_PROJECT}_valkey-socket {LEGACY_PANEL_SOCKET_VOLUME}"
            " valkey-socket remnawave-db-data 2>/dev/null; true"
        ),
        f"docker network rm {PANEL_PROJECT}_internal {LEGACY_PANEL_NETWORK} 2>/dev/null; true",
    ]


@dataclass
class Uninstall:
    """Remove only verified native fleet resources; the host performs its own final database check."""

    controller: bool = False
    generation: int | None = None
    discard: bool = False
    name = "Uninstall Meridian"

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        fleet, token_hash = "", ""
        certs: set[str] = set()
        markers: set[str] = set()
        fragments: set[str] = set()
        topology: list[str | int] = []
        if self.controller:
            cluster = ctx.cluster
            if not cluster or not cluster.fleet_id or not cluster.panel.api_token or not cluster.topology_intent:
                return StepResult(
                    name=self.name, status="failed", detail="Cannot verify this controller's fleet identity."
                )
            fleet, token_hash = cluster.fleet_id, hashlib.sha256(cluster.panel.api_token.encode()).hexdigest()
            intent = cluster.topology_intent
            topology = [cluster.active_generation, cluster.active_plan_hash]
            certs.add(certificate_directory(intent.control.public_hostname or ctx.ip) + "/fullchain.pem")
            for binding in cluster.managed_bindings.values():
                if not binding.active or binding.server_ref != intent.control.server_ref:
                    continue
                if binding.resource_kind == "certificate":
                    certs.add(binding.remote_id)
                elif binding.resource_kind == "nginx_artifact":
                    fragments.add(binding.remote_id)
                elif binding.resource_kind == "firewall_rule":
                    markers.update((firewall_marker(binding.logical_id), legacy_firewall_marker(binding.logical_id)))
        request = [
            fleet,
            token_hash,
            "discard" if self.discard else str(self.generation),
            sorted(certs),
            sorted(markers),
            sorted(fragments),
            topology,
        ]
        script = files("meridian.provision").joinpath("teardown_host.py").read_text(encoding="utf-8")
        result = conn.run(shlex.join(("python3", "-c", script, json.dumps(request))), timeout=120, sensitive=True)
        if result.returncode or result.stdout.strip() != "retired":
            return StepResult(
                name=self.name,
                status="failed",
                detail=(result.stderr.strip().splitlines() or ["The target did not confirm removal."])[-1][:360],
            )
        return StepResult(
            name=self.name, status="changed", detail="Owned services retired; unrelated host resources kept"
        )
