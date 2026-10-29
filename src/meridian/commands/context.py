"""`meridian context` — several fleets on one machine, and the fleet selection every run starts with."""

from __future__ import annotations

import os
import shutil

import typer
from rich.markup import escape

from meridian import config
from meridian.cluster_persistence import saved_backend
from meridian.console import (
    confirm,
    err_console,
    error_context,
    fail,
    has_tty,
    info,
    is_json_mode,
    ok,
    set_json_mode,
    warn,
)
from meridian.contexts import (
    CONTEXT_ENV,
    DEFAULT_CONTEXT,
    ContextError,
    clear_default_fleet,
    context_exists,
    context_home,
    context_names,
    missing_context_error,
    resolve_context,
    save_context,
    saved_context,
    superseded,
    validate_name,
)
from meridian.core.contexts import ContextListResult, ContextSummary
from meridian.core.errors import LocalStateError, RemnawaveFleetError
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.i18n import t
from meridian.renderers import emit_json

# Commands that may start a fleet in a context that does not exist yet (bare `meridian` offers setup).
CREATES_FLEET = frozenset({"", "deploy", "setup", "studio", "restore"})
# Command groups that never read a fleet, so a missing or malformed selection must not stop them.
FLEET_FREE = frozenset({"context", "help", "update", "api", "serve"})
# What a fleet still on Remnawave may run: moving it (deploy offers the move), removing it, and
# commands that never reach its panel. Everything else needs the control service.
REMNAWAVE_FLEET_COMMANDS = frozenset({"migrate", "deploy", "teardown", "server", "fleet.export"})
# What a superseded context may still run (with a warning): commands that change nothing. Anything
# not listed, a new command included, refuses there.
READS_ONLY = frozenset(
    {
        "check",
        "client.list",
        "client.show",
        "client.usage",
        "doctor",
        "fleet.export",
        "fleet.history",
        "fleet.inventory",
        "fleet.status",
        "fleet.usage",
        "logs",
        "node.list",
        "plan",
        "probe",
        "privacy.show",
        "relay.list",
        "route.list",
        "scan",
        "server.list",
        "test",
    }
)


def select_for_invocation(
    *, root: str, leaf: str, command: str, help_requested: bool = False, machine_output: bool = False
) -> None:
    """Bind the fleet this run works on, before any command reads a path (called by the root callback).

    ``machine_output`` (a JSON flag after the command, not parsed yet) makes a refusal a JSON envelope.
    """
    if machine_output:
        set_json_mode(True)
    with error_context(command or "cli"):
        if root and leaf and root != leaf:
            fail(f"Two contexts given: --context {root} and --context {leaf}", hint_type="user")
        fleet_free = command.split(".", 1)[0] in FLEET_FREE
        try:
            selection = config.select_context(leaf or root)
        except ContextError as exc:
            if fleet_free or help_requested:
                return
            fail(exc)
        if fleet_free or help_requested:
            return
        if context_exists(config.INSTALL_HOME, selection.name):
            _gate_superseded(command)
            _gate_remnawave(command)
            return
        if command not in CREATES_FLEET:
            fail(missing_context_error(config.INSTALL_HOME, selection))
        if not is_json_mode():
            info(t("context.creating", name=selection.name))


def _gate_remnawave(command: str) -> None:
    """A fleet still on Remnawave only moves (or goes): every other command points at `meridian migrate`."""
    if command.split(".", 1)[0] in REMNAWAVE_FLEET_COMMANDS or command in REMNAWAVE_FLEET_COMMANDS:
        return
    if saved_backend() == "remnawave":
        fail(RemnawaveFleetError())


def _gate_superseded(command: str) -> None:
    """A context a restore took over only reads: read-only commands warn, every other one refuses."""
    if config.PINNED_HOME:
        return
    marker = superseded(config.FLEET_HOME)
    if marker is None:
        return
    from meridian.fleet_contexts import superseded_message

    message, hint = superseded_message(marker, config.CONTEXT)
    if command not in READS_ONLY:
        fail(message, hint=hint, hint_type="user")
    if not is_json_mode():
        warn(f"{message}. {hint}")


def _current_name() -> str:
    """The context commands use without --context: MERIDIAN_CONTEXT, else the saved one."""
    try:
        return resolve_context(config.INSTALL_HOME, pinned=False, env=os.environ.get(CONTEXT_ENV, "").strip()).name
    except ContextError:
        return DEFAULT_CONTEXT


def _saved_or_default() -> str:
    try:
        return saved_context(config.INSTALL_HOME)
    except ContextError:
        return DEFAULT_CONTEXT


def _refuse_pinned() -> None:
    if config.PINNED_HOME:
        fail(
            "MERIDIAN_HOME is set, so this machine keeps one fleet there",
            hint="Unset MERIDIAN_HOME to keep several fleets as contexts.",
            hint_type="user",
        )


def _require(name: str) -> str:
    try:
        validate_name(name)
    except ContextError as exc:
        fail(exc)
    if not context_exists(config.INSTALL_HOME, name):
        fail(
            f"No context named {name!r}",
            hint=f"Contexts: {', '.join(context_names(config.INSTALL_HOME))}.",
            hint_type="user",
        )
    return name


def _roles(summary: ContextSummary) -> str:
    counts = (
        (summary.exits, "context.exit_one", "context.exit_many"),
        (summary.relays, "context.relay_one", "context.relay_many"),
        (summary.gateways, "context.gateway_one", "context.gateway_many"),
    )
    return ", ".join(t(one if count == 1 else many, count=count) for count, one, many in counts if count)


def run_list() -> None:
    """Show every context with its control server and role counts, from local files only."""
    from meridian.fleet_contexts import summarize_context

    operation = OperationContext()
    with error_context("context.list", timer=operation.timer):
        if config.PINNED_HOME:
            summaries = [summarize_context(DEFAULT_CONTEXT, current=DEFAULT_CONTEXT)]
        else:
            current = _current_name()
            summaries = [summarize_context(name, current=current) for name in context_names(config.INSTALL_HOME)]
        result = ContextListResult(contexts=summaries, pinned=config.PINNED_HOME)
        if is_json_mode():
            emit_json(
                command_envelope(
                    command="context.list",
                    data=result.to_data(),
                    summary=Summary(
                        text=f"{len(summaries)} context(s)", changed=False, counts={"contexts": len(summaries)}
                    ),
                    timer=operation.timer,
                )
            )
            return
        width = max(len(summary.name) for summary in summaries)
        err_console.print()
        for summary in summaries:
            marker = "[bold]*[/bold]" if summary.current else " "
            detail = {
                "ok": escape(_roles(summary)),
                "empty": f"[dim]{escape(t('context.empty'))}[/dim]",
                "unreadable": f"[warn]{escape(t('context.unreadable'))}[/warn]",
            }[summary.state]
            if summary.superseded_by:
                detail = f"[warn]{escape(t('context.superseded', by=summary.superseded_by))}[/warn]"
            control = escape(summary.control or "-")
            err_console.print(f"  {marker} {escape(summary.name):<{width}}  {control:<15}  {detail}")
        err_console.print()
        if config.PINNED_HOME:
            err_console.print(f"  [dim]{escape(t('context.pinned', path=config.HOME_DISPLAY))}[/dim]\n")


def run_use(name: str) -> None:
    """Persist ``name`` as the context every command works on."""
    with error_context("context.use"):
        _refuse_pinned()
        _require(name)
        try:
            save_context(config.INSTALL_HOME, name)
        except OSError as exc:
            fail(f"Could not save the current context: {exc}", hint_type="system")
        ok(t("context.switched", name=name))
        override = os.environ.get(CONTEXT_ENV, "").strip()
        if override and override != name:
            info(t("context.env_override", env=CONTEXT_ENV, name=override))


def run_rename(old: str, new: str) -> None:
    """Rename a named context's directory; servers and fleet files are untouched."""
    with error_context("context.rename"):
        _refuse_pinned()
        if DEFAULT_CONTEXT in (old, new):
            fail(
                "The default context keeps its name",
                hint="It is the Meridian home itself; rename only contexts created with --context.",
                hint_type="user",
            )
        _require(old)
        try:
            validate_name(new)
        except ContextError as exc:
            fail(exc)
        target = context_home(config.INSTALL_HOME, new)
        if target.exists():
            fail(f"A context named {new!r} already exists", hint="Pick another name.", hint_type="user")
        was_current = _saved_or_default() == old
        try:
            context_home(config.INSTALL_HOME, old).rename(target)
            if was_current:
                save_context(config.INSTALL_HOME, new)
        except OSError as exc:
            fail(f"Could not rename the context: {exc}", hint_type="system")
        ok(t("context.renamed", old=old, new=new))


def run_remove(name: str, *, yes: bool = False) -> None:
    """Delete a context's local files; never contacts or changes a server."""
    from meridian.fleet_contexts import fleet_server_hosts

    with error_context("context.remove"):
        _refuse_pinned()
        home = context_home(config.INSTALL_HOME, name)
        # The default context is the Meridian home itself: only a superseded copy of its fleet goes.
        clearing_default = name == DEFAULT_CONTEXT
        if clearing_default and superseded(home) is None:
            fail(
                "The default context cannot be removed",
                hint="It is the Meridian home itself. Remove only contexts created with --context.",
                hint_type="user",
            )
        _require(name)
        if name in {_current_name(), _saved_or_default(), config.CONTEXT}:
            marker = superseded(home)
            fail(
                f"{name!r} is the current context",
                hint=f"Switch first: meridian context use {marker.by if marker and marker.by else 'default'}",
                hint_type="user",
            )
        if clearing_default:
            if not yes and not confirm(t("context.clear_default_confirm"), default=False):
                if not has_tty():
                    err_console.print("  [dim]No terminal to confirm on; pass --yes.[/dim]")
                raise typer.Exit(1)
            try:
                clear_default_fleet(config.INSTALL_HOME)
            except OSError as exc:
                fail(f"Could not remove the default context's fleet files: {exc}", hint_type="system")
            ok(t("context.cleared_default"))
            return
        try:
            servers = len(fleet_server_hosts(home))
        except (LocalStateError, OSError, ValueError):
            servers = -1  # unreadable: still ask, since it may hold a fleet
        if servers and not yes:
            if not has_tty():
                fail(
                    f"Context {name!r} still has servers",
                    hint="Pass --yes to delete only this machine's copy; the servers keep running.",
                    hint_type="user",
                )
            question = (
                t("context.remove_confirm", name=name, count=servers)
                if servers > 0
                else t("context.remove_confirm_unreadable", name=name)
            )
            if not confirm(question, default=False):
                raise typer.Exit(1)
        try:
            shutil.rmtree(home)
        except OSError as exc:
            fail(f"Could not remove {config.display_path(home)}: {exc}", hint_type="system")
        ok(t("context.removed", name=name))
