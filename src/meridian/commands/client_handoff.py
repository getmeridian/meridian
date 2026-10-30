"""Subscription and connection-page handoff for client commands."""

from __future__ import annotations

import re
from collections.abc import Collection

from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.commands._validation import validate_command_input
from meridian.commands.client_state import require_access_user
from meridian.console import err_console, fail
from meridian.control.models import USERNAME_PATTERN
from meridian.control_access import AccessPanel
from meridian.core.command_inputs import ClientNameRequest
from meridian.core.inputs import validate_access_username_value, validate_display_name_value
from meridian.core.topology import assign_access_usernames
from meridian.remnawave import RemnawaveError, User


def format_status(status: str) -> str:
    """Format one Remnawave user status with Rich markup."""
    status_upper = status.upper()
    if status_upper == "ACTIVE":
        return "[green]● active[/green]"
    if status_upper == "DISABLED":
        return "[dim]● disabled[/dim]"
    if status_upper == "LIMITED":
        return "[yellow]● limited[/yellow]"
    if status_upper == "EXPIRED":
        return "[red]● expired[/red]"
    return f"[dim]● {escape(status.lower())}[/dim]"


def validate_client_name(name: str) -> None:
    """Validate one client name at the command boundary."""
    validate_command_input(ClientNameRequest, "Invalid client name", name=name)


def resolve_client_name(
    cluster: ClusterConfig, name: str, *, console_people: bool = False, console_names: Collection[str] = ()
) -> str:
    """The panel username a typed client name means: a username or a display name (Биба).

    `console_people` also accepts a name the control service allows, for a
    person added in its console: such names may hold dots or fall outside Remnawave's lengths.
    `console_names` are the service's usernames: an exact one wins over a case-folded or
    display-name match, and a name that folds onto two people is refused.
    """
    intent = cluster.topology_intent
    declared = list(intent.access.users) if intent is not None else []
    console = console_people and cluster.runs_control_service
    if console and (name in declared or name in console_names):
        return name
    found = intent.access.find_user(name) if intent is not None else None
    if console:
        wanted = name.strip().casefold()
        matches = sorted({*([found] if found else []), *(user for user in console_names if user.casefold() == wanted)})
        if len(matches) > 1:
            fail(
                f"'{name}' could mean {', '.join(matches)}",
                hint="Use the exact username, as `meridian client list` shows it.",
                hint_type="user",
            )
        if matches:
            return matches[0]
    if found is not None:
        return found
    if console and re.fullmatch(USERNAME_PATTERN, name.strip()):
        return name.strip()
    try:
        validate_access_username_value(name)
    except ValueError:
        fail(
            f"Client '{name}' is not one of your fleet's clients",
            hint="Check client names with: meridian client list",
            hint_type="user",
        )
    return name


def resolve_client_mutation_name(cluster: ClusterConfig, panel: AccessPanel, name: str) -> str:
    """Resolve a service-owned edit against the same directory and name rules as client show."""
    if not cluster.runs_control_service:
        name = resolve_client_name(cluster, name)
        validate_client_name(name)
        require_access_user(cluster, name)
        return name
    try:
        names = [user.username for user in panel.list_users()]
    except RemnawaveError as exc:
        fail(f"Could not read clients: {exc}", hint=exc.hint or "Check panel connectivity", hint_type=exc.category)
    return resolve_client_name(cluster, name, console_people=True, console_names=names)


def new_client_usernames(cluster: ClusterConfig, names: list[str]) -> tuple[list[str], dict[str, str]]:
    """Panel usernames for names being added, plus display names where they differ.

    A name the fleet already has maps to its existing username, so the caller can
    refuse it as already declared.
    """
    for name in names:
        try:
            validate_display_name_value(name)
        except ValueError as exc:
            fail(f"Invalid client name '{name}': {exc}", hint_type="user")
    access = cluster.topology_intent.access if cluster.topology_intent is not None else None
    usernames = assign_access_usernames(names, known=access, reserved=access.users if access else ())
    return usernames, {user: name for user, name in zip(usernames, names, strict=True) if user != name}


def get_user_or_fail(panel: AccessPanel, name: str, *, action: str) -> User | None:
    """Read one user while preserving typed CLI failure semantics."""
    try:
        return panel.get_user(name)
    except RemnawaveError as exc:
        fail(
            f"Could not {action} client '{name}': {exc}",
            hint=exc.hint or "Check panel connectivity",
            hint_type=exc.category,
        )


def print_handoff_links(subscription_url: str, *, page_url: str = "") -> None:
    """Print connection-page and subscription links with a terminal QR code."""
    from meridian.urls import generate_qr_terminal

    if page_url:
        err_console.print()
        err_console.print("  [bold]Share this link[/bold]")
        err_console.print(f"  {escape(page_url)}")
        err_console.print("  [dim](They open it, scan the QR code, and connect)[/dim]")
        qr = generate_qr_terminal(page_url)
        if qr:
            err_console.print()
            err_console.print(qr)
        err_console.print()
        err_console.print("  [bold]Subscription URL[/bold] [dim](for Xray/V2Ray apps)[/dim]")
        err_console.print(f"  {escape(subscription_url)}")
    else:
        err_console.print()
        err_console.print("  [bold]Subscription URL[/bold]")
        err_console.print(f"  {escape(subscription_url)}")
        qr = generate_qr_terminal(subscription_url)
        if qr:
            err_console.print()
            err_console.print(qr)
