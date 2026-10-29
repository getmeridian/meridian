"""What each fleet context holds, read from local files only, and the one-fleet-per-server rule.

`meridian.contexts` knows where contexts live; this module opens their `cluster.yml` and
`servers.json` to summarize them for `meridian context` and to stop an apply that would
put a server another context's fleet already uses into this one.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from meridian import config
from meridian.cluster import ClusterConfig
from meridian.compiler.deployment import deployment_server_refs
from meridian.contexts import context_home, context_names
from meridian.core.contexts import ContextSummary
from meridian.core.errors import LocalStateError
from meridian.servers import ServerRegistry


def _fleet(home: Path) -> tuple[ClusterConfig, ServerRegistry]:
    return ClusterConfig.load(home / "cluster.yml"), ServerRegistry(home / "servers.json")


def fleet_server_hosts(home: Path) -> set[str]:
    """Addresses of every server the fleet's reviewed intent puts a role on."""
    cluster, registry = _fleet(home)
    intent = cluster.topology_intent
    if intent is None:
        return {cluster.panel.server_ip} if cluster.panel.server_ip else set()
    hosts: set[str] = set()
    for server_ref in deployment_server_refs(intent):
        entry = registry.find(server_ref)
        hosts.add(entry.host if entry is not None else server_ref)
    return hosts


def summarize_context(name: str, *, current: str) -> ContextSummary:
    """One context's control address and role counts; unreadable files are reported, not raised."""
    home = context_home(config.INSTALL_HOME, name)
    path = config.display_path(home)
    is_current = name == current
    try:
        cluster, registry = _fleet(home)
        intent = cluster.topology_intent
        if intent is None:
            return ContextSummary(
                name=name, current=is_current, path=path, control=cluster.panel.server_ip, state="empty"
            )
        control = registry.find(intent.control.server_ref)
    except (LocalStateError, OSError, ValueError):
        return ContextSummary(name=name, current=is_current, path=path, state="unreadable")
    return ContextSummary(
        name=name,
        current=is_current,
        path=path,
        control=control.host if control is not None else (cluster.panel.server_ip or intent.control.server_ref),
        exits=len(intent.exits),
        relays=len(intent.transparent_relays),
        gateways=len(intent.routing_gateways),
        state="ok",
    )


def refuse_servers_claimed_elsewhere(hosts: Iterable[str]) -> None:
    """Refuse when another context's fleet already holds a role on one of ``hosts``.

    Two fleets on one server would each rewrite its firewall, nginx, and Xray as their own,
    so a server belongs to one fleet's intent at a time. A pinned home has no other contexts.
    A context whose files cannot be read is skipped: its own commands report the damage.
    """
    if config.PINNED_HOME:
        return
    wanted = set(hosts)
    for name in context_names(config.INSTALL_HOME):
        if name == config.CONTEXT:
            continue
        try:
            claimed = wanted & fleet_server_hosts(context_home(config.INSTALL_HOME, name))
        except (LocalStateError, OSError, ValueError):
            continue
        if claimed:
            raise LocalStateError(
                f"{', '.join(sorted(claimed))} already belongs to the fleet in context {name!r}",
                hint=(
                    f"A server serves one fleet at a time. Remove it there first "
                    f"(`meridian --context {name} node remove ...`), or work on that fleet with --context {name}."
                ),
                category="user",
            )
