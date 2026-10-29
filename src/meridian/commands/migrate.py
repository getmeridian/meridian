"""`meridian migrate` (hidden while the control service is): move a Remnawave fleet onto it in place (#95)."""

from __future__ import annotations

from collections import Counter

import typer
from rich.markup import escape

from meridian import config
from meridian.cluster import ClusterConfig
from meridian.commands._helpers import apply_reviewed_intent, load_topology_cluster, persist_reviewed_apply
from meridian.console import confirm, err_console, fail, has_tty, info, ok, warn
from meridian.core.errors import MeridianError
from meridian.core.topology import SetupIntent
from meridian.migrate.flow import ControlMigrator, MigrationOutcome, MigrationReview
from meridian.servers import ServerRegistry


def run(yes: bool = False) -> None:
    cluster = load_topology_cluster()
    registry = ServerRegistry(config.SERVER_PROFILES_FILE)
    migrator = ControlMigrator(cluster, registry, persist=persist_reviewed_apply, on_step=info)
    if migrator.finished:
        _done(migrator.finished_outcome())
        return
    try:
        review = migrator.review()
    except MeridianError as exc:
        fail(exc)
    _render(review, registry)
    if not yes and not confirm("Migrate this fleet now?", default=False):
        if not has_tty():
            err_console.print("  [dim]No terminal to confirm on; pass --yes to migrate without asking.[/dim]")
        raise typer.Exit(1)

    def apply(state: ClusterConfig, intent: SetupIntent, fence_holder: str) -> None:
        apply_reviewed_intent(
            state,
            registry,
            intent,
            fence_holder=fence_holder,
            failure="The Meridian backend did not finish applying",
            hint="Run `meridian migrate` again; it continues from here.",
        )

    try:
        outcome = migrator.run(review, apply)
    except MeridianError as exc:
        fail(exc)
    _done(outcome)


def _render(review: MigrationReview, registry: ServerRegistry) -> None:
    def label(server_ref: str) -> str:
        entry = registry.find(server_ref)
        return escape(entry.name or entry.host) if entry is not None else escape(server_ref)

    print_ = err_console.print
    print_()
    print_("  [bold]Move this fleet from Remnawave to Meridian's control service[/bold]")
    if review.resume_from is not None:
        print_(f"  [dim]Continuing after step: {escape(review.resume_from)}[/dim]")
    print_(f"  Control server: {label(review.intent.control.server_ref)}")
    carried = review.carried
    if carried is not None:
        names = ", ".join(user.username for user in carried.batch.users) or "none"
        print_(f"  [bold]Moves[/bold] {len(carried.batch.users)} users with their links, status, limits, and usage:")
        print_(f"    {escape(names)}")
        for name, notes in sorted(carried.notes.items()):
            print_(f"    [yellow]{escape(name)}[/yellow] loses: {escape('; '.join(notes))}")
        if carried.skipped:
            print_(f"  [bold]Stays behind[/bold] (not Meridian clients): {escape(', '.join(carried.skipped))}")
    print_("  [bold]Keeps[/bold] every subscription URL, Reality key, entry name and order, and connection page.")
    print_("  [bold]Removes[/bold]")
    print_("    the Remnawave panel: its containers, Compose project, and data directory (after a backup)")
    if review.node_servers:
        servers = ", ".join(label(ref) for ref in review.node_servers)
        print_(f"    the panel's node containers on {servers}; Meridian's agent replaces them")
    if review.forgotten:
        kinds = Counter(binding.resource_kind for binding in review.forgotten)
        summary = ", ".join(f"{count} {kind.replace('_', ' ')}" for kind, count in sorted(kinds.items()))
        print_(f"    panel objects that go with its database: {escape(summary)}")
    for binding in review.retired_by_apply:
        print_(f"    [red]remove[/red] {escape(binding.logical_id)}")
    print_("  [bold]Not carried over[/bold]")
    for item in review.dropped:
        print_(f"    {escape(item)}")
    if review.telegram:
        print_("    Telegram notifications")
    home = escape(config.HOME_DISPLAY)
    print_(f"  [dim]A backup goes to {home}/backups and to /var/backups on the control server.[/dim]")
    print_("  [dim]Clients reconnect on their own once the agents start; there is no rollback window.[/dim]")
    print_()


def _done(outcome: MigrationOutcome) -> None:
    if outcome.already_done:
        ok("This fleet already runs on Meridian's control service; nothing to do")
    else:
        ok(f"Migrated {len(outcome.subscription_urls)} users; their links are unchanged")
    if outcome.backup_dir:
        info(f"Backup: {outcome.backup_dir} (and {outcome.server_backup_dir} on the control server)")
    if not outcome.already_done:
        warn("Keep the backup until every client has connected; there is no rollback.")
