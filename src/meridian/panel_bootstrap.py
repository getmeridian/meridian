"""Panel bootstrap and node provisioning — shared by commands and reconciler.

Post-provisioner operations: Remnawave panel API configuration, node
container deployment, host creation. Xray config building lives in
``meridian.xray_config``.
No CLI interaction (prompts, Rich tables) — those stay in commands/.
"""

from __future__ import annotations

import logging
import secrets
import shlex

from meridian import pwa
from meridian.adapters import RemoteExecutorConnection, SSHRemoteExecutor
from meridian.cluster import (
    ClusterConfig,
    InboundRef,
    NodeEntry,
    PanelConfig,
)
from meridian.config import (
    DEFAULT_SNI,
    REMNAWAVE_NODE_API_PORT,
)
from meridian.core.errors import PanelSetupError, ProvisioningError
from meridian.core.execution import RemoteExecutor
from meridian.core.output import OperationContext
from meridian.core.reporters import NoopReporter, Reporter
from meridian.node_deploy import (
    cache_inbounds,
    check_panel_api_ready,
    create_api_token,
    create_hosts_for_node,
    deploy_node_container,
    enforce_host_ordering,
    get_docker_gateway,
    panel_base_url,
    register_or_reuse_node,
    select_default_squad_uuid,
)
from meridian.provision.progress import StepRenderer
from meridian.remnawave import MeridianPanel, RemnawaveError
from meridian.resolve import ResolvedServer
from meridian.ssh import ServerConnection
from meridian.xray_config import build_xray_config

logger = logging.getLogger(__name__)


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

    results = provisioner.run(conn, ctx, reporter=reporter, operation=operation, renderer=renderer)

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
        cluster.save()


def _ensure_config_profile(
    panel: MeridianPanel,
    cluster: ClusterConfig,
    profile_name: str,
    xray_config: dict,
    *,
    raise_on_error: bool = True,
) -> None:
    """Find or create the Xray config profile, cache inbounds."""
    try:
        existing = panel.find_config_profile_by_name(profile_name)
        if existing:
            logger.info("Config profile '%s' already exists, reusing", profile_name)
            cluster.config_profile_uuid = existing.uuid
            cluster.config_profile_name = existing.name
        else:
            profile = panel.create_config_profile(profile_name, xray_config)
            logger.info("Config profile created")
            cluster.config_profile_uuid = profile.uuid
            cluster.config_profile_name = profile.name
    except RemnawaveError as e:
        if raise_on_error:
            raise PanelSetupError(f"Failed to create config profile: {e}")
        logger.warning("Could not update config profile: %s", e)

    cache_inbounds(panel, cluster)


def _ensure_squad_linkage(
    panel: MeridianPanel,
    cluster: ClusterConfig,
    *,
    reuse_uuid: bool = False,
) -> None:
    """Link all cached inbounds to the Default-Squad."""
    try:
        squad_uuid = (cluster.squad_uuid if reuse_uuid else "") or select_default_squad_uuid(
            panel.list_internal_squads()
        )
        if squad_uuid:
            uuids = [ref.uuid for ref in cluster.inbounds.values() if isinstance(ref, InboundRef) and ref.uuid]
            if uuids:
                panel.assign_inbounds_to_squad(squad_uuid, uuids)
                logger.info("Inbounds linked to Default-Squad")
            cluster.squad_uuid = squad_uuid
        elif not reuse_uuid:
            logger.warning("Default-Squad not found — users may not get access to inbounds")
    except RemnawaveError as e:
        logger.warning("Could not configure squad: %s", e)


# Panel configuration workflows
def configure_panel_and_node(
    resolved: ResolvedServer,
    cluster: ClusterConfig,
    domain: str,
    sni: str,
    client_name: str,
    is_first_deploy: bool,
    is_redeploy: bool,
    secret_path: str,
    reality_port: int,
    xhttp_port: int,
    wss_port: int,
    *,
    warp: bool = False,
    hysteria2: bool = True,
    geo_block: bool = True,
    xhttp_path: str = "",
    ws_path: str = "",
    info_page_path: str = "",
) -> None:
    """Configure the Remnawave panel via REST API after containers are running.

    For first deploy: register admin, create config profile, register node,
    create hosts, create first client.

    For redeploy: update Xray config, re-register if needed, redeploy container.
    """
    from meridian import __version__

    logger.info("Configuring panel via API...")

    if is_first_deploy:
        setup_first_deploy(
            resolved=resolved,
            cluster=cluster,
            domain=domain,
            sni=sni,
            client_name=client_name,
            secret_path=secret_path,
            reality_port=reality_port,
            xhttp_port=xhttp_port,
            wss_port=wss_port,
            warp=warp,
            hysteria2=hysteria2,
            geo_block=geo_block,
            version=__version__,
            xhttp_path=xhttp_path,
            ws_path=ws_path,
            info_page_path=info_page_path,
        )
    elif is_redeploy:
        setup_redeploy(
            resolved=resolved,
            cluster=cluster,
            domain=domain,
            sni=sni,
            reality_port=reality_port,
            xhttp_port=xhttp_port,
            wss_port=wss_port,
            version=__version__,
            warp=warp,
            hysteria2=hysteria2,
            geo_block=geo_block,
            xhttp_path=xhttp_path,
            ws_path=ws_path,
        )
    # Note: new-node path removed — deploy refuses new IPs when cluster
    # is configured. Use `meridian node add` instead.


def setup_first_deploy(
    resolved: ResolvedServer,
    cluster: ClusterConfig,
    domain: str,
    sni: str,
    client_name: str,
    secret_path: str,
    reality_port: int,
    xhttp_port: int,
    wss_port: int,
    *,
    warp: bool = False,
    hysteria2: bool = True,
    geo_block: bool,
    version: str,
    xhttp_path: str = "",
    ws_path: str = "",
    info_page_path: str = "",
) -> None:
    """First deploy: full panel bootstrap from scratch."""
    base_url = panel_base_url(resolved.ip, domain, secret_path)

    # Wait for panel API to become accessible
    logger.info("Waiting for panel API...")
    if not check_panel_api_ready(base_url):
        raise PanelSetupError(
            "Panel API is not reachable",
            hint=(
                f"Panel should be at {base_url}\n"
                f"Check: ssh {resolved.user}@{resolved.ip} docker logs remnawave --tail 30"
            ),
        )

    # Register admin user (reuse saved credentials on re-run after partial failure)
    if cluster.panel.admin_user and cluster.panel.admin_pass:
        admin_user = cluster.panel.admin_user
        admin_pass = cluster.panel.admin_pass
    else:
        admin_user = f"meridian-{secrets.token_hex(4)}"
        # Remnawave requires: ≥24 chars, uppercase + lowercase + numbers
        admin_pass = f"Mx{secrets.token_hex(16)}9A"

    try:
        auth_token = MeridianPanel.register_admin(base_url, admin_user, admin_pass)
    except RemnawaveError as e:
        # Admin may already exist on re-run after partial failure — try login
        try:
            auth_token = MeridianPanel.login(base_url, admin_user, admin_pass)
        except RemnawaveError as login_err:
            raise PanelSetupError(
                f"Panel admin registration failed ({e}), login also failed ({login_err})",
                hint="Panel may need a fresh start: meridian teardown, then redeploy",
            )
    logger.info("Panel admin registered")

    # Save admin credentials BEFORE API token step (lockout prevention:
    # if API token creation fails, we can still login with these creds)
    # Reuse the info_page_path generated for the provisioner (nginx uses it),
    # so the connection page URL matches the nginx location.
    sub_path = info_page_path or secrets.token_hex(8)
    cluster.panel = PanelConfig(
        url=base_url,
        api_token="",  # filled after API token creation
        admin_user=admin_user,
        admin_pass=admin_pass,
        server_ip=resolved.ip,
        ssh_user=resolved.user,
        ssh_port=getattr(resolved.conn, "port", 22),
        secret_path=secret_path,
        sub_path=sub_path,
        deployed_with=version,
    )
    cluster.backup()
    cluster.save()  # Create a long-lived API token (auth token is browser-session only)
    api_token = create_api_token(base_url, auth_token)
    logger.info("API token created")

    # Update cluster with the API token
    cluster.panel.api_token = api_token
    cluster.save()

    # Configure subscription page with the real API token
    _update_subscription_page(resolved.conn, cluster, api_token)
    logger.info("Subscription page configured")

    with MeridianPanel(base_url, api_token) as panel:
        # Create config profile (Xray inbound definitions)
        xray_result = build_xray_config(
            resolved.conn,
            sni=sni,
            reality_port=reality_port,
            xhttp_port=xhttp_port,
            wss_port=wss_port,
            domain=domain,
            geo_block=geo_block,
            warp=warp,
            hysteria2=hysteria2,
            xhttp_path=xhttp_path,
            ws_path=ws_path,
        )
        reality_public_key = xray_result.reality_public_key
        reality_short_id = xray_result.reality_short_id
        reality_private_key = xray_result.reality_private_key

        _ensure_config_profile(panel, cluster, "meridian-default", xray_result.config)
        _ensure_squad_linkage(panel, cluster)

        # Register this server as a node
        node_address = get_docker_gateway(resolved.conn)
        node_name = domain or resolved.ip
        try:
            node_creds = register_or_reuse_node(panel, cluster, node_address, node_name)
        except RemnawaveError as e:
            raise PanelSetupError(f"Failed to register node: {e}")

        if not deploy_node_container(resolved.conn, node_creds.secret_key):
            raise ProvisioningError(
                "Node container did not become healthy",
                hint=(f"Check: ssh {shlex.quote(resolved.user)}@{resolved.ip} docker logs remnawave-node --tail 50"),
            )

        # Save node entry to cluster
        node_entry = NodeEntry(
            ip=resolved.ip,
            uuid=node_creds.uuid,
            name=node_name,
            ssh_user=resolved.user,
            ssh_port=getattr(resolved.conn, "port", 22),
            sni=sni,
            domain=domain,
            is_panel_host=True,
            deployed_with=version,
            warp=warp,
            xhttp_path=xhttp_path,
            ws_path=ws_path,
            reality_public_key=reality_public_key,
            reality_short_id=reality_short_id,
            reality_private_key=reality_private_key,
        )
        # Deduplicate: update existing entry or append new
        cluster.remove_node(resolved.ip)
        cluster.nodes.append(node_entry)
        cluster.backup()
        cluster.save()

        # Create direct hosts for this node's protocols
        create_hosts_for_node(panel, cluster, resolved.ip, domain, sni)
        enforce_host_ordering(panel)

        # Create first client
        try:
            existing_user = panel.get_user(client_name)
            if existing_user:
                logger.info("Client '%s' already exists", client_name)
                user = existing_user
            else:
                squad_uuids = [cluster.squad_uuid] if cluster.squad_uuid else None
                user = panel.create_user(client_name, squad_uuids=squad_uuids)
                logger.info("Client '%s' created", client_name)

            # Deploy connection page for this client
            if user and isinstance(getattr(user, "vless_uuid", None), str) and user.vless_uuid:
                try:
                    sub_url = panel.get_subscription_url(user.short_uuid) if user.short_uuid else ""
                    page_url = pwa.deploy_client_page(
                        resolved.conn, cluster, node_entry, user.vless_uuid, client_name, sub_url
                    )
                    if page_url:
                        logger.info("Connection page deployed")
                        cluster._extra["_page_url"] = page_url
                    if sub_url:
                        cluster._extra["_subscription_url"] = sub_url
                except (OSError, RuntimeError):
                    pass  # Non-fatal — subscription URL still works
        except RemnawaveError as e:
            logger.warning("Could not create client '%s': %s", client_name, e)

    logger.info("Panel configuration complete")


def setup_redeploy(
    resolved: ResolvedServer,
    cluster: ClusterConfig,
    domain: str,
    sni: str,
    reality_port: int,
    xhttp_port: int,
    wss_port: int,
    version: str,
    *,
    warp: bool = False,
    hysteria2: bool = True,
    geo_block: bool = True,
    xhttp_path: str = "",
    ws_path: str = "",
) -> None:
    """Redeploy: update node config, rebuild Xray profile, redeploy container.

    Preserves Reality keys from cluster.yml so existing client configs
    continue to work. Re-runs panel API configuration to pick up
    changes to SNI, domain, protocol options, etc.
    """
    if not cluster.panel.api_token:
        raise PanelSetupError(
            "Cluster config exists but has no API token",
            hint="Run: meridian teardown, then redeploy from scratch",
        )

    node = cluster.find_node(resolved.ip)
    if not node:
        raise PanelSetupError(f"Node {resolved.ip} not found in cluster config")

    try:
        with MeridianPanel(cluster.panel.url, cluster.panel.api_token) as panel:
            if not panel.ping():
                raise PanelSetupError(
                    "Cannot reach panel API",
                    hint=f"Panel URL: {cluster.panel.url}\nCheck panel logs on {cluster.panel.server_ip}",
                )
            logger.info("Panel API accessible")

            # Build Xray config reusing existing Reality keys
            # If we have saved keys, skip keygen (preserves client configs)
            reality_key_material = (
                node.reality_private_key,
                node.reality_public_key,
                node.reality_short_id,
            )
            if all(reality_key_material):
                logger.info("Reusing existing Reality keys (client configs preserved)")
                xray_result = build_xray_config(
                    None,  # no SSH needed when reusing keys
                    sni=sni or node.sni,
                    reality_port=reality_port,
                    xhttp_port=xhttp_port,
                    wss_port=wss_port,
                    domain=domain or node.domain,
                    geo_block=geo_block,
                    warp=warp,
                    hysteria2=hysteria2,
                    xhttp_path=xhttp_path or node.xhttp_path,
                    ws_path=ws_path or node.ws_path,
                    existing_private_key=node.reality_private_key,
                    existing_public_key=node.reality_public_key,
                    existing_short_id=node.reality_short_id,
                )
            elif any(reality_key_material):
                raise PanelSetupError(
                    "Reality key material is incomplete; refusing to rotate keys during redeploy",
                    hint="Restore reality_private_key, reality_public_key, and reality_short_id in cluster.yml.",
                    category="user",
                )
            else:
                # No saved keys — must regenerate (breaks existing client configs)
                logger.warning("No saved Reality keys — regenerating (clients will need new configs)")
                xray_result = build_xray_config(
                    resolved.conn,
                    sni=sni or node.sni,
                    reality_port=reality_port,
                    xhttp_port=xhttp_port,
                    wss_port=wss_port,
                    domain=domain or node.domain,
                    geo_block=geo_block,
                    warp=warp,
                    hysteria2=hysteria2,
                    xhttp_path=xhttp_path or node.xhttp_path,
                    ws_path=ws_path or node.ws_path,
                )

            xray_config = xray_result.config
            reality_public_key = xray_result.reality_public_key
            reality_short_id = xray_result.reality_short_id
            reality_private_key = xray_result.reality_private_key

            profile_name = cluster.config_profile_name or "meridian-default"
            _ensure_config_profile(panel, cluster, profile_name, xray_config, raise_on_error=False)
            _ensure_squad_linkage(panel, cluster, reuse_uuid=True)

            # Verify node registration
            if node.uuid:
                api_node = panel.get_node(node.uuid)
                if api_node:
                    logger.info("Node %s still registered", resolved.ip)
                else:
                    logger.warning("Node %s not found in panel — re-registering", resolved.ip)
                    inbound_uuids = [
                        ref.uuid for ref in cluster.inbounds.values() if isinstance(ref, InboundRef) and ref.uuid
                    ]
                    # Panel-host nodes must use Docker gateway address (panel
                    # runs in bridge network, can't reach 127.0.0.1 on host).
                    if node.is_panel_host:
                        node_address = get_docker_gateway(resolved.conn)
                    else:
                        node_address = resolved.ip
                    node_creds = panel.create_node(
                        name=domain or resolved.ip,
                        address=node_address,
                        port=REMNAWAVE_NODE_API_PORT,
                        config_profile_uuid=cluster.config_profile_uuid,
                        inbound_uuids=inbound_uuids,
                    )
                    node.uuid = node_creds.uuid
                    if not deploy_node_container(resolved.conn, node_creds.secret_key):
                        raise ProvisioningError(
                            "Node container did not become healthy",
                            hint=(
                                f"Check: ssh {shlex.quote(resolved.user)}@{resolved.ip} "
                                "docker logs remnawave-node --tail 50"
                            ),
                        )
            else:
                logger.warning("Node has no UUID — skipping panel verification")

            # Redeploy node container (refresh secret key + image)
            if node.uuid:
                secret_key = panel.get_node_secret_key()
                if secret_key:
                    if not deploy_node_container(resolved.conn, secret_key):
                        raise ProvisioningError(
                            "Node container did not become healthy",
                            hint=(
                                f"Check: ssh {shlex.quote(resolved.user)}@{resolved.ip} "
                                "docker logs remnawave-node --tail 50"
                            ),
                        )

            # Recreate hosts (idempotent)
            # Use provided values; only fall back to stored values if caller passed ""
            # (which means "not specified" from imperative commands, or "clear" from apply)
            effective_domain = domain if domain else node.domain
            effective_sni = sni if sni else node.sni
            create_hosts_for_node(panel, cluster, resolved.ip, effective_domain, effective_sni)
            enforce_host_ordering(panel)

            # Update node metadata
            # For sni/domain: empty string from declarative apply means "clear",
            # non-empty means "set". Imperative commands always pass the resolved value.
            if sni:
                node.sni = sni
            if domain:
                node.domain = domain
            node.deployed_with = version
            node.warp = warp
            node.reality_public_key = reality_public_key
            node.reality_short_id = reality_short_id
            node.reality_private_key = reality_private_key
            node.xhttp_path = xhttp_path or node.xhttp_path
            node.ws_path = ws_path or node.ws_path
            cluster.backup()
            cluster.save()
            logger.info("Node configuration updated")

            # Ensure subscription page has a valid token (upgrade from pre-subscription deploys)
            if node.is_panel_host:
                _update_subscription_page(resolved.conn, cluster, cluster.panel.api_token)

    except RemnawaveError as e:
        raise PanelSetupError(f"Panel API error: {e}") from e


def setup_new_node(
    resolved: ResolvedServer,
    cluster: ClusterConfig,
    domain: str,
    sni: str,
    reality_port: int,
    xhttp_port: int,
    wss_port: int,
    version: str,
    *,
    xhttp_path: str = "",
    ws_path: str = "",
) -> None:
    """Add a new node to an existing cluster."""
    if not cluster.panel.api_token:
        raise PanelSetupError(
            "No panel configured -- deploy a panel first with: meridian deploy <IP>",
            category="user",
        )

    try:
        with MeridianPanel(cluster.panel.url, cluster.panel.api_token) as panel:
            if not panel.ping():
                raise PanelSetupError(
                    "Cannot reach panel API",
                    hint=f"Panel URL: {cluster.panel.url}\nCheck panel logs on {cluster.panel.server_ip}",
                )

            node_name = domain or resolved.ip
            node_creds = register_or_reuse_node(panel, cluster, resolved.ip, node_name)

            # Deploy the node container with the secret key
            deploy_node_container(resolved.conn, node_creds.secret_key)

            # Save node entry
            # Reuse Reality keys from existing node (shared config profile)
            existing_node = cluster.panel_node
            node_entry = NodeEntry(
                ip=resolved.ip,
                uuid=node_creds.uuid,
                name=node_name,
                ssh_user=resolved.user,
                ssh_port=getattr(resolved.conn, "port", 22),
                sni=sni,
                domain=domain,
                is_panel_host=False,
                deployed_with=version,
                xhttp_path=xhttp_path,
                ws_path=ws_path,
                reality_public_key=existing_node.reality_public_key if existing_node else "",
                reality_short_id=existing_node.reality_short_id if existing_node else "",
            )
            cluster.remove_node(resolved.ip)
            cluster.nodes.append(node_entry)
            cluster.backup()
            cluster.save()

            # Create hosts for the new node
            create_hosts_for_node(panel, cluster, resolved.ip, domain, sni)
            enforce_host_ordering(panel)

    except RemnawaveError as e:
        raise PanelSetupError(f"Panel API error: {e}") from e

    logger.info("New node configured")
