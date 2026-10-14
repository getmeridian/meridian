"""Move pre-rename panel and node runtimes to their neutral names (see ``meridian.host_runtime``).

The panel's state is its directory: PostgreSQL's ``data/`` bind mount and the
``.env`` secrets. Migration renames that directory in one ``rename(2)`` on the
same filesystem, never copies or deletes it, so the bytes stay the same and an
interrupted run resumes: either the old directory is still in place (stop the
old containers again, then move it) or it has moved (deploy as usual).
"""

from __future__ import annotations

import shlex

from meridian.host_runtime import (
    LEGACY_NODE_DIR,
    LEGACY_PANEL_DIR,
    LEGACY_PANEL_NETWORK,
    LEGACY_PANEL_SOCKET_VOLUME,
    NODE,
    PANEL_DIR,
    PANEL_STOP_ORDER,
)
from meridian.ssh import ServerConnection

_PROBE_DONE = "runtime-probe-done"
LEGACY_PANEL_DIR_ABSENT = f"test ! -e {shlex.quote(LEGACY_PANEL_DIR)}"


def legacy_panel_probe() -> str:
    """Report the legacy and neutral panel directories, mounts under the legacy one, and legacy containers.

    ``docker ps`` must answer: with the daemon down, a container that is merely
    stopped would restart later against a moved directory.
    """
    legacy, current = shlex.quote(LEGACY_PANEL_DIR), shlex.quote(PANEL_DIR)
    names = " ".join(shlex.quote(container.legacy) for container in PANEL_STOP_ORDER)
    return (
        f"[ -e {legacy} ] && echo legacy-dir; [ -e {current} ] && echo current-dir; "
        f'awk -v d={legacy} \'$2 == d || index($2, d "/") == 1 {{print "mount:" $2}}\' /proc/mounts 2>/dev/null; '
        "running=$(docker ps -a --format '{{.Names}}') || exit 3; "
        f'for c in {names}; do printf \'%s\\n\' "$running" | grep -qx "$c" && echo "container:$c"; done; '
        f"echo {_PROBE_DONE}"
    )


def adopt_legacy_panel(conn: ServerConnection) -> str:
    """Stop a pre-rename panel stack and move its directory to ``PANEL_DIR``.

    Returns an empty string when the panel is (now) under the neutral name, or
    why the migration stopped. Nothing is changed unless the outcome is certain:
    both directories present, or a mount at or below the legacy one, refuse
    before any container stops.
    """
    probe = conn.run(legacy_panel_probe(), timeout=30)
    lines = probe.stdout.split()
    if probe.returncode != 0 or _PROBE_DONE not in lines:
        detail = (probe.stderr or probe.stdout).strip()[:200]
        return f"could not inspect the panel directories and containers: {detail or f'exit {probe.returncode}'}"
    has_legacy_dir = "legacy-dir" in lines
    if has_legacy_dir and "current-dir" in lines:
        return (
            f"both {LEGACY_PANEL_DIR} and {PANEL_DIR} exist; keep the one holding the panel's data/ and .env "
            f"as {PANEL_DIR}, move the other aside, then retry"
        )
    if has_legacy_dir and any(line.startswith("mount:") for line in lines):
        return f"{LEGACY_PANEL_DIR} is or contains a mount point; mount it at {PANEL_DIR} instead, then retry"
    if any(line.startswith("container:") for line in lines):
        stopped = conn.run(_stop_legacy_panel_command(), timeout=300)
        if stopped.returncode != 0:
            detail = (stopped.stderr or stopped.stdout).strip()[:200]
            return f"could not stop the pre-rename panel containers: {detail or f'exit {stopped.returncode}'}"
    if not has_legacy_dir:
        return ""
    legacy, current = shlex.quote(LEGACY_PANEL_DIR), shlex.quote(PANEL_DIR)
    # Same parent, so one rename(2): never a copy, never a partial directory.
    moved = conn.run(
        f"test ! -e {current} && mv {legacy} {current} && test -d {current} && test ! -e {legacy}", timeout=60
    )
    if moved.returncode != 0:
        detail = (moved.stderr or moved.stdout).strip()[:200]
        return f"could not move {LEGACY_PANEL_DIR} to {PANEL_DIR}: {detail or f'exit {moved.returncode}'}"
    return ""


def _stop_legacy_panel_command() -> str:
    """Stop (gracefully, dependents first) and remove the legacy containers, their network and socket volume."""
    names = " ".join(shlex.quote(container.legacy) for container in PANEL_STOP_ORDER)
    return (
        f"for c in {names}; do"
        ' if docker inspect --type container "$c" >/dev/null 2>&1; then'
        ' docker stop -t 60 "$c" >/dev/null && docker rm "$c" >/dev/null || exit 1; fi; done;'
        f" docker network rm {shlex.quote(LEGACY_PANEL_NETWORK)} >/dev/null 2>&1;"
        f" docker volume rm {shlex.quote(LEGACY_PANEL_SOCKET_VOLUME)} >/dev/null 2>&1; true"
    )


# The node keeps nothing worth moving: its secret comes from the panel on every deploy.
# Prints ``container`` and/or ``dir`` for what survives under the pre-rename names.
LEGACY_NODE_PROBE = (
    f"docker inspect --type container {shlex.quote(NODE.legacy)} >/dev/null 2>&1 && echo container;"
    f" [ -e {shlex.quote(LEGACY_NODE_DIR)} ] && echo dir; true"
)


def stop_legacy_node(conn: ServerConnection) -> bool:
    """Stop the pre-rename node so the renamed one can bind the same host ports; True when it is stopped."""
    name = shlex.quote(NODE.legacy)
    result = conn.run(
        f"if docker inspect --type container {name} >/dev/null 2>&1; then docker stop -t 30 {name} >/dev/null; fi",
        timeout=60,
    )
    return result.returncode == 0


def restart_legacy_node(conn: ServerConnection, node_dir: str) -> None:
    """Undo a failed rename: drop the renamed node and start the pre-rename one again."""
    conn.run("docker compose down 2>/dev/null; true", cwd=node_dir, timeout=60)
    conn.run(f"docker start {shlex.quote(NODE.legacy)} >/dev/null 2>&1; true", timeout=60)


def remove_legacy_node_command() -> str:
    """Remove the pre-rename node container and directory; exits nonzero if either survives."""
    name, directory = shlex.quote(NODE.legacy), shlex.quote(LEGACY_NODE_DIR)
    return (
        f"if docker inspect --type container {name} >/dev/null 2>&1; then docker rm -f {name} >/dev/null || exit 1; fi;"
        f" rm -rf {directory} && test ! -e {directory}"
    )
