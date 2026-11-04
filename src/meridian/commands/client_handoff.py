"""Subscription and connection-page handoff for client commands."""

from __future__ import annotations

from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.console import err_console, fail
from meridian.control_access import AccessPanel
from meridian.core.errors import MeridianError
from meridian.core.inputs import validate_display_name_value
from meridian.i18n import isolate, t
from meridian.remnawave import User


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


def resolve_client_name(cluster: ClusterConfig, name: str, **_options: object) -> str:
    """Resolve a live service username or display name through a bounded query."""
    from meridian.control_access import access_panel

    try:
        validate_display_name_value(name)
        with access_panel(cluster) as panel:
            return resolve_client_mutation_name(cluster, panel, name)
    except MeridianError as exc:
        fail(exc)
    except ValueError as exc:
        fail(f"Invalid client name: {exc}", hint_type="user")


def resolve_client_mutation_name(cluster: ClusterConfig, panel: AccessPanel, name: str) -> str:
    """Exact username wins, then a folded live username or display name; ambiguity refuses."""
    try:
        name = validate_display_name_value(name)
    except ValueError as exc:
        fail(f"Invalid client name: {exc}", hint_type="user")
    try:
        matches = panel.matching_users(name)
    except MeridianError as exc:
        fail(exc)
    if len(matches) > 1:
        fail(
            t(
                "client.name_ambiguous",
                name=isolate(name),
                people=", ".join(isolate(user.username) for user in matches),
            ),
            hint=t("client.exact_name_hint"),
            hint_type="user",
        )
    if not matches:
        fail(f"Client '{name}' not found", hint="Check names with: meridian client list", hint_type="user")
    return matches[0].username


def get_user_or_fail(panel: AccessPanel, name: str, *, action: str) -> User | None:
    """Read one user while preserving typed CLI failure semantics."""
    try:
        return panel.get_user(name)
    except MeridianError as exc:
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
