"""Hand the fleet from Remnawave to the control service, and remove Remnawave once the service serves it.

The cut-over stops the panel (kept on disk) and removes the panel-managed node
containers before the reviewed apply: the agents' cores bind the same inbound
ports, and the service the same loopback port as the panel. Panel-side objects
(Hosts, Profiles, squads, users, settings, templates) live in the panel's
database, which the backup holds and the last step deletes, so their bindings
are forgotten rather than retired through an API that no longer answers.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler.models import ResourcePlan
from meridian.host_runtime import LEGACY_NODE_DIR, LEGACY_PANEL_DIR, NODE, NODE_DIR, PANEL_DIR, PANEL_STOP_ORDER
from meridian.migrate import MigrationError
from meridian.provision.uninstall import panel_stack_removal
from meridian.ssh import ServerConnection

# Kinds whose whole state is in the panel's database (or, for probes, nowhere).
PANEL_SIDE_KINDS: frozenset[str] = frozenset(
    {
        "access_user",
        "service_user",
        "host",
        "external_squad",
        "internal_squad",
        "inbound",
        "node_binding",
        "config_profile",
        "subscription_template",
        "subscription_settings",
        "control_plane_runtime",
        "probe",
        "routing_gateway",
        "egress_pool",
        "route_rule",
    }
)
# The Remnawave bootstrap's attestation; the panel goes, so it does too.
CONTROL_RUNTIME_ATTESTATION_PATH = "/var/lib/meridian/control-runtime.attestation"
# Every panel container under its neutral and pre-rename name, dependents first.
_PANEL_NAMES = tuple(shlex.quote(name) for container in PANEL_STOP_ORDER for name in (container.name, container.legacy))


def panel_bindings(cluster: ClusterConfig, plan: ResourcePlan) -> list[ManagedResourceBinding]:
    """Active panel-side bindings the Meridian plan no longer has: they go with the panel's database."""
    kept = {resource.logical_id for resource in plan.resources}
    return sorted(
        (
            binding
            for binding in cluster.managed_bindings.values()
            if binding.active and binding.resource_kind in PANEL_SIDE_KINDS and binding.logical_id not in kept
        ),
        key=lambda binding: (binding.resource_kind, binding.logical_id),
    )


def node_runtime_bindings(cluster: ClusterConfig) -> list[ManagedResourceBinding]:
    """Panel node containers: committed ones, and any an uncommitted apply installed (still inactive)."""
    return sorted(
        (
            binding
            for binding in cluster.managed_bindings.values()
            if binding.resource_kind == "node_runtime"
            and (binding.active or binding.generation > cluster.active_generation)
        ),
        key=lambda binding: (binding.server_ref, binding.generation),
    )


def stop_panel(conn: ServerConnection) -> None:
    """Stop the panel stack, dependents first, and keep it from coming back on reboot; its data stays."""
    result = conn.run(
        f"for c in {' '.join(_PANEL_NAMES)}; do"
        ' if docker inspect --type container "$c" >/dev/null 2>&1; then'
        ' docker update --restart=no "$c" >/dev/null && docker stop -t 60 "$c" >/dev/null || exit 1; fi; done',
        timeout=600,
        operation_name="stop the panel",
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:200]
        raise MigrationError(
            f"Could not stop the panel: {detail or f'exit {result.returncode}'}",
            hint="Check `docker ps` on the control server, then run `meridian migrate` again.",
        )


# The node API firewall rule under its neutral and pre-rename comment.
NODE_API_RULE_COMMENTS = ("fw-node-api", "Meridian node API (Docker internal)")
_NODE_REPOSITORY = "remnawave/node"


def remove_node_runtime(conn: ServerConnection) -> None:
    """Stop and delete the node container, its Compose directory, image, and API rule, under both names.

    Docker itself stays: the baseline installed it, and the operator may run other containers.
    """
    compose = shlex.quote(f"{NODE_DIR}/docker-compose.yml")
    names = " ".join(shlex.quote(name) for name in (NODE.name, NODE.legacy))
    directories = " ".join(shlex.quote(path) for path in (NODE_DIR, LEGACY_NODE_DIR))
    result = conn.run(
        f"if [ -f {compose} ]; then docker compose -f {compose} down --remove-orphans --volumes || exit 1; fi;"
        f' for c in {names}; do if docker inspect --type container "$c" >/dev/null 2>&1;'
        ' then docker rm -f "$c" >/dev/null || exit 1; fi; done;'
        f' rm -rf {directories} && for d in {directories}; do test ! -e "$d" || exit 1; done;'
        # Images still used by another container refuse removal; that is fine.
        f" ids=$(docker images -q {shlex.quote(_NODE_REPOSITORY)} 2>/dev/null | sort -u);"
        ' if [ -n "$ids" ]; then docker image rm $ids >/dev/null 2>&1; fi; true',
        timeout=120,
    )
    if result.returncode != 0:
        detail = _detail(result.stderr or result.stdout, result.returncode)
        raise MigrationError(f"Could not remove the Remnawave node runtime: {detail}")
    patterns = " ".join(f"-e {shlex.quote('# ' + comment)}" for comment in NODE_API_RULE_COMMENTS)
    rules = conn.run(
        f"ufw status numbered 2>/dev/null | grep -F {patterns}"
        r" | sed -n 's/^\[ *\([0-9][0-9]*\)\].*/\1/p'"
        ' | sort -rn | while read -r rule; do ufw --force delete "$rule" >/dev/null || exit 1; done',
        timeout=30,
    )
    if rules.returncode != 0:
        raise MigrationError("Could not remove the node API firewall rule.")


def remove_node_runtimes(bindings: list[ManagedResourceBinding], connections: Mapping[str, ServerConnection]) -> None:
    """Remove each exit's panel-managed node container (and its node API rule)."""
    # One removal per server: every node binding there names the same container and directory.
    for binding in {binding.server_ref: binding for binding in bindings}.values():
        try:
            remove_node_runtime(connections[binding.server_ref])
        except Exception as exc:  # noqa: BLE001 - reported with the server it failed on
            raise MigrationError(
                f"Could not remove the node container on {binding.server_ref}: {exc}",
                hint="Check the server over SSH, then run `meridian migrate` again.",
            ) from exc


def forget(bindings: list[ManagedResourceBinding]) -> None:
    for binding in bindings:
        binding.active = False


def remove_panel(conn: ServerConnection) -> None:
    """Delete the panel stack, its data directory, and the panel bootstrap's attestation, then prove they are gone.

    Raises unless a completed inspection finds no panel container or directory, so a failed removal
    (or a lost connection) leaves the migration at `applied` for a re-run instead of marking it done.
    """
    for command in [*panel_stack_removal(), f"rm -f {shlex.quote(CONTROL_RUNTIME_ATTESTATION_PATH)}"]:
        result = conn.run(command, timeout=300, operation_name="remove the panel")
        if result.returncode != 0:
            raise MigrationError(
                f"Could not remove the panel: {_detail(result.stderr or result.stdout, result.returncode)}",
                hint="Check the control server over SSH, then run `meridian migrate` again.",
            )
    directories = " ".join(shlex.quote(path) for path in (PANEL_DIR, LEGACY_PANEL_DIR))
    left = conn.run(
        "names=$(docker ps -a --format '{{.Names}}') || exit 3;"
        f" printf '%s\\n' \"$names\" | grep -x -e {' -e '.join(_PANEL_NAMES)};"
        f' for d in {directories}; do if [ -e "$d" ]; then echo "$d"; fi; done; true',
        timeout=30,
        operation_name="check the panel is gone",
    )
    if left.returncode != 0:
        raise MigrationError(
            f"Could not check that the panel is gone: {_detail(left.stderr or left.stdout, left.returncode)}",
            hint="Check the control server over SSH, then run `meridian migrate` again.",
        )
    if left.stdout.strip():
        raise MigrationError(
            "Panel containers or directories are still present after removal: " + ", ".join(left.stdout.split()),
            hint="Remove them (`docker rm -f`, `rm -rf`), then run `meridian migrate` again.",
        )


def _detail(output: str, returncode: int) -> str:
    return output.strip()[:200] or f"exit {returncode}"


def drop_panel_state(cluster: ClusterConfig) -> None:
    """Forget what only the panel used; topology, Reality keys, allocations, and paths stay."""
    cluster.panel.admin_user = ""
    cluster.panel.admin_pass = ""
    cluster.subscription_page = None
    cluster.telegram = None
    cluster.image_pins = {}
    for workload in cluster.workloads:
        workload.config_profile_uuid = ""
        workload.node_uuids = {}
        workload.inbound_uuids = {}
        workload.host_uuids = {}
    for key, binding in list(cluster.managed_bindings.items()):
        if not binding.active and binding.resource_kind in PANEL_SIDE_KINDS | {"node_runtime"}:
            del cluster.managed_bindings[key]
