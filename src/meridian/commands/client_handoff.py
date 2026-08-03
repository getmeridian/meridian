"""Evidence-backed subscription and legacy connection-page handoff."""

from __future__ import annotations

import shlex
from dataclasses import dataclass

from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.commands._helpers import make_panel
from meridian.commands._validation import validate_command_input
from meridian.commands.client_state import persist_client_state
from meridian.console import err_console, fail
from meridian.core.clients import PanelUserLike
from meridian.core.command_inputs import ClientNameRequest
from meridian.core.redaction import redact_string
from meridian.diagnostics import command_evidence_unavailable
from meridian.remnawave import MeridianPanel, RemnawaveError, User
from meridian.ssh import ServerConnection, SSHError


@dataclass(frozen=True)
class PageResolution:
    """Connection-page evidence plus any failed post-repair persistence."""

    url: str = ""
    repaired: bool = False
    persistence_error: str = ""


@dataclass(frozen=True)
class PageCleanup:
    """Outcome of removing a legacy page after its panel user was deleted."""

    state_changed: bool = False
    error: str = ""


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


def build_page_url(cluster: ClusterConfig, vless_uuid: str, *, require_deployed: bool = True) -> str:
    """Build a connection-page URL only when its required evidence exists."""
    from meridian.pwa import connection_page_deployed

    info_page_path = cluster.panel.sub_path or ""
    if not info_page_path or not vless_uuid:
        return ""
    if require_deployed and not connection_page_deployed(cluster, vless_uuid):
        return ""
    node = cluster.panel_node
    if not node:
        return ""
    host = node.domain or node.ip
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"https://{host}/{info_page_path}/{vless_uuid}/"


def resolve_page_url(
    cluster: ClusterConfig,
    user: PanelUserLike,
    *,
    repair: bool,
) -> PageResolution:
    """Return an evidenced page URL and whether this call regenerated it."""
    from meridian.pwa import (
        connection_page_deployed,
        connection_page_failed,
        mark_connection_page_deployed,
        mark_connection_page_failed,
    )

    vless_uuid = user.vless_uuid
    candidate = build_page_url(cluster, vless_uuid, require_deployed=False)
    failed = connection_page_failed(cluster, vless_uuid)
    if not candidate:
        return PageResolution()
    if connection_page_deployed(cluster, vless_uuid) and not repair:
        return PageResolution(url=candidate)
    if failed and not repair:
        return PageResolution()
    if not cluster.panel.server_ip:
        return PageResolution()
    panel_node = cluster.panel_node
    if panel_node is None:
        return PageResolution()
    persistence_error = ""
    if not failed:
        try:
            with ServerConnection(
                cluster.panel.server_ip,
                user=cluster.panel.ssh_user or "root",
                port=getattr(cluster.panel, "ssh_port", 22) or 22,
            ) as conn:
                result = conn.run(
                    f"test -f /var/www/private/{shlex.quote(vless_uuid)}/index.html",
                    timeout=10,
                )
            if result.returncode == 0:
                mark_connection_page_deployed(cluster, vless_uuid)
                persistence_error = persist_client_state(cluster)
                return PageResolution(url=candidate, persistence_error=persistence_error)
            if command_evidence_unavailable(result.returncode):
                if not repair:
                    return PageResolution()
            else:
                mark_connection_page_failed(cluster, vless_uuid)
                persistence_error = persist_client_state(cluster)
        except (OSError, RuntimeError, SSHError):
            if not repair:
                return PageResolution()
    if not repair:
        return PageResolution(persistence_error=persistence_error)
    try:
        from meridian.pwa import deploy_client_page

        with ServerConnection(
            cluster.panel.server_ip,
            user=cluster.panel.ssh_user or "root",
            port=getattr(cluster.panel, "ssh_port", 22) or 22,
        ) as conn:
            with make_panel(cluster) as panel:
                subscription_url = panel.get_subscription_url(user.short_uuid) if user.short_uuid else ""
            repaired_url = deploy_client_page(
                conn,
                cluster,
                panel_node,
                vless_uuid,
                user.username,
                subscription_url,
            )
        if repaired_url:
            mark_connection_page_deployed(cluster, vless_uuid)
            persistence_error = persist_client_state(cluster)
            return PageResolution(
                url=repaired_url,
                repaired=True,
                persistence_error=persistence_error,
            )
        mark_connection_page_failed(cluster, vless_uuid)
        persistence_error = persist_client_state(cluster)
    except (OSError, RuntimeError, SSHError, RemnawaveError):
        return PageResolution(persistence_error=persistence_error)
    return PageResolution(persistence_error=persistence_error)


def cleanup_client_page(client: User, cluster: ClusterConfig) -> PageCleanup:
    """Remove legacy page files without hiding an unconfirmed cleanup."""
    if not client.vless_uuid:
        return PageCleanup()
    from meridian.pwa import connection_page_deployed, connection_page_failed

    page_configured = bool(
        cluster.panel.sub_path
        or connection_page_deployed(cluster, client.vless_uuid)
        or connection_page_failed(cluster, client.vless_uuid)
    )
    if not page_configured:
        return PageCleanup()
    if not cluster.panel.server_ip:
        return PageCleanup(error="panel server address is unavailable")
    try:
        with ServerConnection(
            cluster.panel.server_ip,
            user=cluster.panel.ssh_user or "root",
            port=getattr(cluster.panel, "ssh_port", 22) or 22,
        ) as conn:
            result = conn.run(
                f"rm -rf /var/www/private/{shlex.quote(client.vless_uuid)}",
                timeout=15,
            )
        if result.returncode == 0:
            from meridian.pwa import forget_connection_page

            forget_connection_page(cluster, client.vless_uuid)
            return PageCleanup(state_changed=True)
        detail = redact_string(result.stderr.strip() or result.stdout.strip())
        return PageCleanup(error=detail or f"remote cleanup exited {result.returncode}")
    except (OSError, RuntimeError, SSHError) as exc:
        return PageCleanup(error=redact_string(str(exc)) or type(exc).__name__)


def print_subscription(panel: MeridianPanel, user: User, *, page_url: str = "") -> str:
    """Print a handoff, returning a redacted error after successful mutation."""
    try:
        subscription_url = panel.get_subscription_url(user.short_uuid)
    except RemnawaveError as exc:
        return redact_string(str(exc))
    print_handoff_links(subscription_url, page_url=page_url)
    return ""


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
