"""`meridian check --repair`: bring a stopped or broken control service back without an apply (#124).

An apply refuses while the service is down, since the apply fence lives in it.
This reviews the control server, confirms, and reinstalls the same package with
the same configuration (`provision/control_repair.py`); nothing an apply owns
changes.
"""

from __future__ import annotations

import typer
from rich.markup import escape

from meridian import config
from meridian.commands._helpers import load_topology_cluster, require_intent
from meridian.console import confirm, err_console, error_context, fail, has_tty, info, is_json_mode, ok
from meridian.control_artifacts import service_package
from meridian.core.errors import MeridianError
from meridian.fleet_contexts import refuse_if_superseded
from meridian.host_runtime import CONTROL_SERVICE_DIR, CONTROL_SERVICE_UNIT
from meridian.provision.control_repair import RepairReview, repair, review_repair
from meridian.resolve import connection_for_entry, local_entry, merge_target
from meridian.servers import ServerEntry, ServerRegistry
from meridian.ssh import SSHError
from meridian.ssh_ui import RichSSHUI


def run(target: str = "", *, user: str = "", ssh_port: int = 0, yes: bool = False) -> None:
    with error_context("check"):
        if is_json_mode():
            fail("--repair has no JSON output", hint="Run `meridian check --repair` without --json.", hint_type="user")
        cluster = load_topology_cluster(warn_adoption=False)
        try:
            refuse_if_superseded()
        except MeridianError as exc:
            fail(exc)
        registry = ServerRegistry(config.SERVER_PROFILES_FILE)
        control_ref = require_intent(cluster).control.server_ref
        entry = _control_entry(registry, control_ref, target, user=user, port=ssh_port)
        label = _label(entry)
        conn = connection_for_entry(entry, user=user, port=ssh_port)
        try:
            conn.check_ssh(ui=RichSSHUI())
        except (OSError, SSHError) as exc:
            fail(
                f"Cannot connect to the control server {label}: {exc}",
                hint=getattr(exc, "hint", "") or "Check the saved SSH user and port: meridian server list",
                hint_type="system",
            )
        try:
            review = review_repair(conn, service_package(), fleet_id=cluster.fleet_id, host=entry.host)
        except MeridianError as exc:
            fail(exc)
        _render(review, label)
        if not yes and not confirm("Repair the control service now?", default=False):
            if not has_tty():
                err_console.print("  [dim]No terminal to confirm on; pass --yes to repair without asking.[/dim]")
            raise typer.Exit(1)
        info("Reinstalling the control service (this can take a few minutes)")
        result = repair(conn, review)
        if result.status == "failed":
            fail(
                f"The control service on {label} is still not answering: {result.detail}",
                hint=f"See its log: meridian logs {entry.host} --component panel",
                hint_type="system",
            )
        ok(f"The control service on {label} answers again")
        info("Run `meridian check` to see the rest of the fleet, then apply as usual.")


def _control_entry(registry: ServerRegistry, control_ref: str, target: str, *, user: str, port: int) -> ServerEntry:
    control = registry.find(control_ref)
    if control is None:
        fail(
            f"The fleet's control server {control_ref!r} is not a saved server",
            hint="Add it back with its SSH details: meridian server add",
            hint_type="user",
        )
    if not target:
        return control
    try:
        spec = merge_target(target, user=user, port=port)
    except ValueError as exc:
        fail(str(exc), hint="Use IP, saved name, user@host[:port], or 'local'.", hint_type="user")
    named = local_entry(registry) if spec.local else registry.find(spec.host)
    if named is None or named.id != control.id:
        fail(
            f"{target} is not this fleet's control server",
            hint=f"--repair repairs the control service only. Its server is {_label(control)}: meridian check --repair",
            hint_type="user",
        )
    return control


def _label(entry: ServerEntry) -> str:
    return f"{entry.name} ({entry.host})" if entry.name and entry.name != entry.host else entry.host


def _render(review: RepairReview, label: str) -> None:
    print_ = err_console.print
    package = review.package
    what = f"meridian-vpn {package.version}" + (f" from this checkout ({package.wheel_name})" if package.wheel else "")
    print_()
    print_(f"  [bold]Repair the control service on {escape(label)}[/bold]")
    print_(f"  It does not answer. Meridian reinstalls {escape(what)} under {CONTROL_SERVICE_DIR}, keeps the")
    print_(f"  server's own configuration, rewrites {CONTROL_SERVICE_UNIT}, restarts it, and waits until it answers.")
    if not review.recorded:
        print_("  [dim]The server has no record of which version it ran.[/dim]")
    print_(f"  Its database ({escape(review.database)}) stays as it is: topology, people, node documents,")
    print_("  the apply fence, and revisions do not change.")
    print_()
