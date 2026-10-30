"""`meridian logs`: recent, redacted logs from the components one fleet server runs."""

from __future__ import annotations

import typer

from meridian.commands._helpers import load_topology_cluster, require_intent
from meridian.commands.health import server_roles
from meridian.commands.resolve import ensure_server_connection, resolve_server
from meridian.console import fail, info, warn
from meridian.core.topology import SetupIntent
from meridian.diagnostics.logs import LOG_COMPONENTS, LogSource, LogTail, collect_log, log_sources
from meridian.servers import ServerRegistry


def run(
    target: str = "",
    *,
    user: str = "",
    ssh_port: int = 0,
    lines: int,
    pattern: str = "",
    components: list[str] | None = None,
) -> None:
    """Print each component's recent log lines; exit 3 when any log could not be read."""
    if any(char in pattern for char in "\n\r\0"):
        fail("--grep takes one line of text", hint="Search for one fixed string at a time.", hint_type="user")
    unknown = sorted(set(components or ()) - set(LOG_COMPONENTS))
    if unknown:
        fail(
            f"Unknown component: {', '.join(unknown)}",
            hint=f"Use one of: {', '.join(LOG_COMPONENTS)}.",
            hint_type="user",
        )
    from meridian.config import SERVER_PROFILES_FILE

    cluster = load_topology_cluster()
    intent = require_intent(cluster)
    registry = ServerRegistry(SERVER_PROFILES_FILE)
    resolved = resolve_server(registry, explicit_ip=target, user=user, port=ssh_port)
    entry = registry.find(resolved.ip)
    roles = server_roles(intent, entry.id) if entry is not None else []
    if entry is None or not roles:
        fail(
            f"{resolved.ip} holds no role in the fleet, so Meridian keeps no logs there",
            hint="See which servers run what: meridian fleet status",
            hint_type="user",
        )
    sources = log_sources(roles, relay_units=_relay_units(intent, entry.id) if "relay" in roles else ())
    if components:
        held = sorted({source.component for source in sources})
        sources = [source for source in sources if source.component in components]
        if not sources:
            fail(
                f"{resolved.ip} runs none of: {', '.join(components)}",
                hint=f"Its roles ({', '.join(roles)}) keep logs for: {', '.join(held)}.",
                hint_type="user",
            )

    info(f"Roles on {entry.name or resolved.ip}: {', '.join(roles)}")
    resolved = ensure_server_connection(resolved)
    known_secrets = cluster.secret_values()
    unavailable = 0
    for source in sources:
        tail = collect_log(resolved.conn, source, lines=lines, pattern=pattern, known_secrets=known_secrets)
        _render(tail, pattern=pattern)
        unavailable += not tail.available
    if unavailable:
        warn(f"{unavailable} of {len(sources)} logs could not be read")
        raise typer.Exit(3)


def _relay_units(intent: SetupIntent, server_ref: str) -> list[tuple[str, str]]:
    """Each Realm hop unit this server runs, with its pre-rename name, from the compiled topology."""
    from meridian.compiler import compile_topology
    from meridian.compiler.models import RealmHopPayload
    from meridian.reconciler.server_render import legacy_realm_service_name, realm_service_name

    return [
        (realm_service_name(resource.logical_id), legacy_realm_service_name(resource.logical_id))
        for resource in compile_topology(intent).resources
        if isinstance(resource.payload, RealmHopPayload) and resource.payload.server_ref == server_ref
    ]


def _render(tail: LogTail, *, pattern: str) -> None:
    """Plain text on stdout, never Rich: log lines are untrusted and may contain markup."""
    typer.echo(f"==> {_title(tail.source)} <==")
    if not tail.available:
        typer.echo(f"(unavailable: {tail.error})")
    elif not tail.lines:
        typer.echo(f"(no lines containing {pattern!r})" if pattern else "(empty)")
    for log_line in tail.lines:
        typer.echo(log_line)
    typer.echo("")


def _title(source: LogSource) -> str:
    return f"{source.component}: {source.kind} {source.name}"
