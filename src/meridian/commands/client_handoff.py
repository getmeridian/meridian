"""Evidence-backed subscription and connection-page handoff for client commands."""

from __future__ import annotations

from dataclasses import dataclass

from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.commands._validation import validate_command_input
from meridian.commands.client_state import persist_client_state
from meridian.console import err_console, fail
from meridian.core.clients import PanelUserLike
from meridian.core.command_inputs import ClientNameRequest
from meridian.remnawave import MeridianPanel, RemnawaveError, User
from meridian.ssh import SSHError


@dataclass(frozen=True)
class PageResolution:
    """Connection-page evidence plus any failed post-repair persistence."""

    url: str = ""
    repaired: bool = False
    persistence_error: str = ""


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


def get_user_or_fail(panel: MeridianPanel, name: str, *, action: str) -> User | None:
    """Read one user while preserving typed CLI failure semantics."""
    try:
        return panel.get_user(name)
    except RemnawaveError as exc:
        fail(
            f"Could not {action} client '{name}': {exc}",
            hint=exc.hint or "Check panel connectivity",
            hint_type=exc.category,
        )


def resolve_page_url(
    cluster: ClusterConfig,
    user: PanelUserLike,
    *,
    repair: bool,
) -> PageResolution:
    """Return an evidenced connection-page URL, republishing it when asked."""
    from meridian.handoff import page_url, publish_connection_pages
    from meridian.pwa import connection_page_deployed

    candidate = page_url(cluster, user)
    if not candidate:
        return PageResolution()
    if not repair:
        return PageResolution(url=candidate if connection_page_deployed(cluster, user.vless_uuid) else "")
    try:
        links = publish_connection_pages(cluster, [user.username])
    except (OSError, RuntimeError, SSHError, RemnawaveError):
        return PageResolution()
    if not links or not links[0].page_url:
        return PageResolution()
    return PageResolution(
        url=links[0].page_url,
        repaired=True,
        persistence_error=persist_client_state(cluster),
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
