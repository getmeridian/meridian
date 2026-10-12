"""Server provisioning and control-plane bootstrap for the topology runtime.

``run_provisioner`` prepares a server over SSH (OS, Docker, nginx, TLS);
``ensure_control_plane_access`` obtains panel credentials. Compiled
resources own everything else. No CLI interaction lives here.
"""

from __future__ import annotations

import logging
import secrets

from meridian.adapters import RemoteExecutorConnection, SSHRemoteExecutor
from meridian.cluster import (
    ClusterConfig,
    ClusterConfigExternallyModifiedError,
    PanelConfig,
)
from meridian.config import (
    DEFAULT_SNI,
)
from meridian.core.cancellation import CancellationToken
from meridian.core.errors import PanelSetupError, ProvisioningError
from meridian.core.execution import RemoteExecutor
from meridian.core.output import OperationContext
from meridian.core.reporters import NoopReporter, Reporter
from meridian.node_deploy import (
    check_panel_api_ready,
    create_api_token,
    panel_base_url,
)
from meridian.provision.progress import StepRenderer
from meridian.remnawave import MeridianPanel, RemnawaveError
from meridian.resolve import ResolvedServer
from meridian.ssh import ServerConnection

logger = logging.getLogger(__name__)


def _persist_remote_state_checkpoint(cluster: ClusterConfig, *, backup: bool = False) -> None:
    """Save local state after a panel or node mutation without losing provenance."""
    try:
        if backup:
            cluster.backup()
        cluster.save()
    except (ClusterConfigExternallyModifiedError, OSError, ValueError) as exc:
        raise PanelSetupError(
            "Remote panel or node state changed, but Meridian could not save cluster.yml",
            hint=(
                f"{exc} Repair local state permissions or disk space, inspect the panel and server, then "
                "recover or save matching cluster state before retrying."
            ),
        ) from exc


# Provisioner pipeline
def run_provisioner(
    resolved: ResolvedServer,
    cluster: ClusterConfig,
    domain: str,
    sni: str,
    harden: bool,
    is_panel_host: bool,
    secret_path: str,
    xhttp_port: int,
    reality_port: int,
    wss_port: int,
    *,
    warp: bool = False,
    hysteria2: bool = True,
    geo_block: bool = True,
    xhttp_path: str = "",
    ws_path: str = "",
    info_page_path: str = "",
    reporter: Reporter = NoopReporter(),
    operation: OperationContext | None = None,
    remote_executor: RemoteExecutor | None = None,
    renderer: StepRenderer | None = None,
    cancellation: CancellationToken | None = None,
) -> None:
    """Run the SSH-based provisioner pipeline (OS, Docker, containers)."""
    from meridian.provision import ProvisionContext, Provisioner, build_node_steps, build_setup_steps

    ctx = ProvisionContext(
        ip=resolved.ip,
        user=resolved.user,
        domain=domain,
        sni=sni or DEFAULT_SNI,
        xhttp_enabled=True,
        warp=warp,
        hysteria2=hysteria2,
        geo_block=geo_block,
        hosted_page=True,
        harden=harden,
        is_panel_host=is_panel_host,
    )
    ctx.xhttp_port = xhttp_port
    ctx.reality_port = reality_port
    ctx.wss_port = wss_port

    # Store cluster and generated paths for provisioner steps
    ctx.cluster = cluster
    # nginx needs these paths for reverse proxy and connection page locations
    ctx.web_base_path = secret_path
    ctx.info_page_path = info_page_path or cluster.panel.sub_path or secrets.token_hex(8)
    # Provide xhttp/ws paths — reuse saved paths on redeploy, generate fresh otherwise
    ctx.xhttp_path = xhttp_path or secrets.token_hex(8)
    ctx.ws_path = ws_path or secrets.token_hex(8)
    # Subscription page path for nginx reverse proxy — reuse if already set,
    # otherwise generate. The v4 panel stack always deploys the subscription
    # page container, so we always need a stable path persisted to cluster.yml
    # (without persisting, the next REMOVE_SUBSCRIPTION_PAGE would skip nginx
    # cleanup and a subsequent re-enable would add a duplicate nginx route).
    from meridian.cluster import SubscriptionPageConfig

    sub_page_path = ""
    if cluster.subscription_page and cluster.subscription_page.path:
        sub_page_path = cluster.subscription_page.path
    sub_page_path = sub_page_path or secrets.token_hex(8)
    ctx.subscription_page_path = sub_page_path
    if cluster.subscription_page is None:
        cluster.subscription_page = SubscriptionPageConfig(path=sub_page_path)
    else:
        cluster.subscription_page.path = sub_page_path

    logger.info("Configuring server at %s...", ctx.ip)
    if domain:
        logger.info("Domain: %s", domain)
    if sni and sni != DEFAULT_SNI:
        logger.info("SNI: %s", sni)
    if warp:
        logger.info("Cloudflare WARP: enabled")
    if not geo_block:
        logger.info("Geo-blocking: disabled (Russian sites accessible)")

    # Choose pipeline: full setup (panel + node) or node-only
    if is_panel_host:
        steps = build_setup_steps(ctx)
    else:
        steps = build_node_steps(ctx)

    provisioner = Provisioner(steps)

    if remote_executor is None and not isinstance(resolved.conn, ServerConnection):
        raise ProvisioningError("No SSH connection available", category="bug")
    remote_executor = remote_executor or SSHRemoteExecutor(resolved.conn)
    conn = RemoteExecutorConnection(remote_executor)

    results = provisioner.run(
        conn,
        ctx,
        reporter=reporter,
        operation=operation,
        renderer=renderer,
        cancellation=cancellation,
    )

    # Check for failures
    failed = [r for r in results if r.status == "failed"]
    if failed:
        raise ProvisioningError(
            "Setup failed",
            hint=f"Step '{failed[0].name}' failed: {failed[0].detail}\nRun: meridian preflight {ctx.ip}",
        )

    logger.info("All provisioning steps completed")


# Shared setup helpers — deduplicated from setup_first_deploy / setup_redeploy
def _update_subscription_page(
    conn: ServerConnection,
    cluster: ClusterConfig,
    api_token: str,
) -> None:
    """Write subscription page env with real API token and mark deployed."""
    from meridian.cluster import SubscriptionPageConfig
    from meridian.provision.remnawave_panel import configure_subscription_page

    if configure_subscription_page(conn, api_token):
        if cluster.subscription_page is None:
            cluster.subscription_page = SubscriptionPageConfig()
        cluster.subscription_page.deployed = True
        _persist_remote_state_checkpoint(cluster)


def ensure_control_plane_access(
    *,
    resolved: ResolvedServer,
    cluster: ClusterConfig,
    domain: str,
    secret_path: str,
    info_page_path: str,
    version: str,
) -> None:
    """Bootstrap only panel credentials; compiled resources own the topology."""
    base_url = panel_base_url(resolved.ip, domain, secret_path)
    if cluster.panel.url == base_url and cluster.panel.api_token:
        with MeridianPanel(base_url, cluster.panel.api_token) as panel:
            if panel.ping():
                return

    logger.info("Waiting for panel API...")
    if not check_panel_api_ready(base_url):
        raise PanelSetupError(
            "Panel API is not reachable",
            hint=(
                f"Panel should be at {base_url}\n"
                f"Check: ssh {resolved.user}@{resolved.ip} "
                "docker logs remnawave --tail 30"
            ),
        )

    if cluster.panel.admin_user and cluster.panel.admin_pass:
        admin_user = cluster.panel.admin_user
        admin_pass = cluster.panel.admin_pass
    else:
        admin_user = f"meridian-{secrets.token_hex(4)}"
        admin_pass = f"Mx{secrets.token_hex(16)}9A"

    try:
        auth_token = MeridianPanel.register_admin(
            base_url,
            admin_user,
            admin_pass,
        )
    except RemnawaveError as exc:
        try:
            auth_token = MeridianPanel.login(
                base_url,
                admin_user,
                admin_pass,
            )
        except RemnawaveError as login_error:
            raise PanelSetupError(
                f"Panel admin registration failed ({exc}), login also failed ({login_error})",
                hint="Recover the saved panel credentials or redeploy the panel.",
            ) from login_error

    cluster.panel = PanelConfig(
        url=base_url,
        api_token="",
        admin_user=admin_user,
        admin_pass=admin_pass,
        server_ip=resolved.ip,
        ssh_user=resolved.user,
        ssh_port=getattr(resolved.conn, "port", 22),
        secret_path=secret_path,
        sub_path=info_page_path or cluster.panel.sub_path or secrets.token_hex(8),
        deployed_with=version,
    )
    _persist_remote_state_checkpoint(cluster, backup=True)
    cluster.panel.api_token = create_api_token(base_url, auth_token)
    _persist_remote_state_checkpoint(cluster)
    _update_subscription_page(
        resolved.conn,
        cluster,
        cluster.panel.api_token,
    )
