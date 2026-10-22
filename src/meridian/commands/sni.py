"""`meridian node sni` and `meridian relay sni` -- give an exit or relay a new camouflage SNI.

The change replaces the old name outright. A Reality listener sends probes to exactly
one site, so a lingering old name would answer with the new site's certificate, and
the usual reason to switch is that the old name is blocked anyway.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer
from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.commands._helpers import (
    apply_reviewed_intent,
    load_topology_cluster,
    require_intent,
    resolve_exit,
    reviewed_runtime,
)
from meridian.commands._validation import validate_command_input
from meridian.commands.relay import select_relay_chains
from meridian.compiler.models import SUBSCRIPTION_UPDATE_INTERVAL_HOURS
from meridian.console import confirm, err_console, fail, info, ok, warn
from meridian.core.command_inputs import CamouflageChangeRequest
from meridian.core.errors import LocalStateError, MeridianError
from meridian.core.topology import ProtocolPathIntent, SetupIntent, TransparentRelayIntent
from meridian.i18n import t
from meridian.ssh import SSHError
from meridian.ssh_ui import RichSSHUI

if TYPE_CHECKING:
    from meridian.core.execution import ServerConnection
    from meridian.servers import ServerRegistry


def run_exit_sni(exit_arg: str, sni: str, *, self_steal: bool = False, yes: bool = False) -> None:
    """Point an exit's Reality path at a new camouflage site, then apply the reviewed change."""
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.servers import ServerRegistry
    from meridian.setup.editor import set_exit_reality_sni

    request = validate_command_input(
        CamouflageChangeRequest,
        "Invalid node sni request",
        target=exit_arg,
        sni=sni,
        self_steal=self_steal,
        yes=yes,
    )
    if not request.self_steal:
        refuse_unusable_camouflage(request.sni)
    cluster = load_topology_cluster()
    intent = require_intent(cluster)
    exit_ = resolve_exit(intent, request.target)
    path = _reality_path(intent, exit_.id)
    subject = t("sni.exit_subject", exit=exit_.id)
    if (path.reality_sni, path.self_steal) == (request.sni, request.self_steal):
        ok(t("sni.unchanged", subject=subject, sni=request.sni))
        return
    try:
        updated = set_exit_reality_sni(intent, exit_.id, request.sni, self_steal=request.self_steal)
    except ValueError as exc:
        fail(str(exc), hint="Check: meridian node list", hint_type="user")

    registry = ServerRegistry(SERVER_PROFILES_FILE)
    relays = [
        relay for relay in intent.transparent_relays if (relay.exit_ref, relay.protocol_path_ref) == (exit_.id, path.id)
    ]
    following = sorted(relay.id for relay in relays if not relay.reality_sni)
    keeping = sorted(relay.id for relay in relays if relay.reality_sni)
    built_on = [gateway for gateway in intent.routing_gateways if gateway.bridge_path_ref == path.id]
    gateways = sorted(gateway.id for gateway in built_on)
    if request.self_steal:
        # The exit serves the name itself once applied, so only its DNS can be checked now.
        require_own_hostname(registry, exit_.server_ref, request.sni)
    else:
        # A gateway built on the path imitates the same site from its own server.
        for server_ref in dict.fromkeys([exit_.server_ref, *(gateway.server_ref for gateway in built_on)]):
            require_reachable_camouflage(registry, server_ref, request.sni)
    lines = [
        *([t("sni.self_steal_line", sni=request.sni)] if request.self_steal else []),
        *([t("sni.followers", relays=", ".join(following))] if following else []),
        *([t("sni.own_sni", relays=", ".join(keeping))] if keeping else []),
        *([t("sni.gateways", gateways=", ".join(gateways))] if gateways else []),
    ]
    _review_and_apply(
        cluster, registry, updated, subject=subject, old=path.reality_sni, new=request.sni, lines=lines, yes=request.yes
    )


def run_relay_sni(relay_arg: str, sni: str, *, exit_arg: str = "", yes: bool = False) -> None:
    """Give the relay chains through a server (or one chain by ID) a new camouflage SNI."""
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.servers import ServerRegistry
    from meridian.setup.editor import set_relay_reality_sni

    request = validate_command_input(
        CamouflageChangeRequest,
        "Invalid relay sni request",
        target=relay_arg,
        sni=sni,
        exit_arg=exit_arg,
        yes=yes,
    )
    refuse_unusable_camouflage(request.sni)
    cluster = load_topology_cluster()
    intent = require_intent(cluster)
    registry = ServerRegistry(SERVER_PROFILES_FILE)
    chains, _server_ref, label = select_relay_chains(intent, registry, request.target, request.exit_arg)
    ids = sorted(relay.id for relay in chains)
    subject = t("sni.relay_subject", relays=", ".join(ids))
    if label not in ids:
        # One chain through a named server reads best as that server's name.
        if len(ids) == 1:
            subject = t("sni.relay_subject", relays=label)
        else:
            subject = t("sni.relay_through", subject=subject, server=label)
    current = sorted({_effective_sni(intent, relay) for relay in chains})
    if current == [request.sni]:
        ok(t("sni.unchanged", subject=subject, sni=request.sni))
        return
    try:
        updated = set_relay_reality_sni(intent, set(ids), request.sni)
    except ValueError as exc:
        fail(str(exc), hint="Check: meridian relay list", hint_type="user")

    # A relay's own SNI gets a Reality listener on its exit, which dials the site from there.
    exits = {exit_.id: exit_ for exit_ in intent.exits}
    for exit_ref in sorted({relay.exit_ref for relay in chains}):
        if _reality_path(intent, exit_ref).reality_sni != request.sni:
            require_reachable_camouflage(registry, exits[exit_ref].server_ref, request.sni)
    _review_and_apply(
        cluster, registry, updated, subject=subject, old=", ".join(current), new=request.sni, lines=[], yes=request.yes
    )


def refuse_unusable_camouflage(sni: str) -> None:
    """Fail for a name Reality can never imitate from a VPS, before any connection opens."""
    from meridian.diagnostics.camouflage import camouflage_refusal

    reason = camouflage_refusal(sni)
    if reason == "network":
        fail(t("sni.refused_network", sni=sni), hint=t("sni.refused_hint"), hint_type="user")
    if reason == "certificate":
        fail(t("sni.refused_certificate", sni=sni), hint=t("sni.refused_hint"), hint_type="user")


def require_reachable_camouflage(registry: ServerRegistry, server_ref: str, sni: str) -> None:
    """Fail unless the server reaches ``sni`` over TLS 1.3 with HTTP/2, the check Reality targets pass."""
    from meridian.diagnostics.preflight import reachable_reality_targets

    conn, label, _ = _saved_server(registry, server_ref)
    info(t("sni.checking", server=label, sni=sni))
    if not reachable_reality_targets(conn, (sni,)):
        fail(
            t("sni.unreachable", server=label, sni=sni),
            hint=t("sni.unreachable_hint", server=label),
            hint_type="user",
        )


def _review_and_apply(
    cluster: ClusterConfig,
    registry: ServerRegistry,
    updated: SetupIntent,
    *,
    subject: str,
    old: str,
    new: str,
    lines: list[str],
    yes: bool,
) -> None:
    """Show what the SNI change touches, confirm, then apply exactly that review."""
    from meridian.core.setup import SetupDraft

    try:
        review = reviewed_runtime(registry, cluster).review(SetupDraft.from_intent(updated), cluster=cluster)
    except MeridianError as exc:
        fail(exc)
    refresh = t("sni.refresh", hours=SUBSCRIPTION_UPDATE_INTERVAL_HOURS)
    err_console.print()
    err_console.print(f"  [bold]{escape(t('sni.review_title', subject=subject, old=old, new=new))}[/bold]")
    for line in [*lines, refresh]:
        err_console.print(f"  {escape(line)}")
    if review.retiring:
        err_console.print(f"  {escape(t('sni.retiring', count=len(review.retiring)))}")
        for binding in review.retiring:
            err_console.print(f"    [red]remove[/red] {escape(binding.logical_id)}")
    err_console.print(f"  [dim]Review {review.review_hash}[/dim]")
    err_console.print()
    if not yes and not confirm(t("sni.confirm", subject=subject, sni=new)):
        raise typer.Exit(1)

    apply_reviewed_intent(
        cluster,
        registry,
        updated,
        failure=t("sni.failed", subject=subject),
        hint=t("sni.failed_hint", sni=new),
        expected_plan_hash=review.review_hash,
    )
    ok(t("sni.changed", subject=subject, sni=new))
    info(refresh)
    err_console.print(f"  [dim]{escape(t('sni.check_it'))}[/dim]")
    err_console.print()


def _reality_path(intent: SetupIntent, exit_id: str) -> ProtocolPathIntent:
    exit_ = next(item for item in intent.exits if item.id == exit_id)
    return next(path for path in exit_.paths if path.protocol == "reality")


def _effective_sni(intent: SetupIntent, relay: TransparentRelayIntent) -> str:
    """The name a relay's subscription entry announces: its own, else its exit path's."""
    if relay.reality_sni:
        return relay.reality_sni
    exit_ = next(item for item in intent.exits if item.id == relay.exit_ref)
    return next(path.reality_sni for path in exit_.paths if path.id == relay.protocol_path_ref)


def require_own_hostname(registry: ServerRegistry, server_ref: str, hostname: str) -> None:
    """Check a self-steal hostname's DNS from the saved server it must point at."""
    conn, label, address = _saved_server(registry, server_ref)
    check_own_hostname(conn, hostname, server_ip=address, label=label)


def check_own_hostname(conn: ServerConnection, hostname: str, *, server_ip: str, label: str) -> None:
    """Fail when a self-steal hostname does not resolve; warn when it points somewhere else.

    Let's Encrypt must reach the exit through the name, and the camouflage only looks
    natural when the name really lives on the exit's address.
    """
    from meridian.diagnostics.camouflage import own_hostname

    info(t("sni.own_checking", server=label, sni=hostname))
    found = own_hostname(conn, hostname, server_ip)
    if found.state == "unresolved":
        fail(
            t("sni.own_unresolved", sni=hostname),
            hint=t("sni.own_unresolved_hint", sni=hostname, ip=server_ip),
            hint_type="user",
        )
    if found.state == "elsewhere":
        warn(t("sni.own_elsewhere", sni=hostname, addresses=", ".join(found.addresses), ip=server_ip))
    elif found.state == "unknown":
        warn(t("sni.own_unknown", server=label, sni=hostname))


def _saved_server(registry: ServerRegistry, server_ref: str) -> tuple[ServerConnection, str, str]:
    """An SSH-checked connection to a topology server, its label, and its address."""
    from meridian.resolve import connection_for_entry

    try:
        entry = registry.find(server_ref)
    except LocalStateError as exc:
        fail(exc)
    if entry is None:
        fail(t("sni.server_missing", server=server_ref), hint="Check: meridian server list", hint_type="user")
    conn = connection_for_entry(entry)
    try:
        conn.check_ssh(ui=RichSSHUI())
    except SSHError as exc:
        fail(exc)
    return conn, entry.name or entry.host, entry.host
