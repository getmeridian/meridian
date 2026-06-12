"""Panel bootstrap and node provisioning — shared by commands and reconciler.

Post-provisioner operations: Remnawave panel API configuration, node
container deployment, host creation. Xray config building lives in
``meridian.xray_config``.
No CLI interaction (prompts, Rich tables) — those stay in commands/.
"""

from __future__ import annotations

import secrets
import shlex
import time
from typing import Any

from meridian.adapters import RemoteExecutorConnection, SSHRemoteExecutor
from meridian.cluster import (
    ClusterConfig,
    InboundRef,
    NodeEntry,
    PanelConfig,
    ProtocolKey,
)
from meridian.config import (
    DEFAULT_SNI,
    REMNAWAVE_NODE_API_PORT,
)
from meridian.console import (
    err_console,
    fail,
    info,
    is_quiet_mode,
    ok,
    warn,
)
from meridian.core.execution import RemoteExecutor
from meridian.core.output import OperationContext
from meridian.core.reporters import NoopReporter, Reporter
from meridian.remnawave import MeridianPanel, NodeCredentials, RemnawaveError
from meridian.resolve import ResolvedServer
from meridian.ssh import ServerConnection
from meridian.xray_config import build_xray_config

# ---------------------------------------------------------------------------
# Provisioner pipeline
# ---------------------------------------------------------------------------


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
    pq: bool = False,
    warp: bool = False,
    geo_block: bool = True,
    xhttp_path: str = "",
    ws_path: str = "",
    info_page_path: str = "",
    reporter: Reporter = NoopReporter(),
    operation: OperationContext | None = None,
    remote_executor: RemoteExecutor | None = None,
) -> None:
    """Run the SSH-based provisioner pipeline (OS, Docker, containers)."""
    from meridian.provision import ProvisionContext, Provisioner, build_node_steps, build_setup_steps

    ctx = ProvisionContext(
        ip=resolved.ip,
        user=resolved.user,
        domain=domain,
        sni=sni or DEFAULT_SNI,
        xhttp_enabled=True,
        pq_encryption=pq,
        warp=warp,
        geo_block=geo_block,
        hosted_page=True,
        harden=harden,
        is_panel_host=is_panel_host,
    )
    ctx.xhttp_port = xhttp_port
    ctx.reality_port = reality_port
    ctx.wss_port = wss_port

    # Store cluster and secret path in context for provisioner steps
    ctx.cluster = cluster
    ctx["secret_path"] = secret_path
    # nginx needs these paths for reverse proxy and connection page locations
    ctx["web_base_path"] = secret_path
    ctx["info_page_path"] = info_page_path or cluster.panel.sub_path or secrets.token_hex(8)
    # Provide xhttp/ws paths — reuse saved paths on redeploy, generate fresh otherwise
    ctx["xhttp_path"] = xhttp_path or secrets.token_hex(8)
    ctx["ws_path"] = ws_path or secrets.token_hex(8)
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
    ctx["subscription_page_path"] = sub_page_path
    if cluster.subscription_page is None:
        cluster.subscription_page = SubscriptionPageConfig(path=sub_page_path)
    else:
        cluster.subscription_page.path = sub_page_path

    if not is_quiet_mode():
        err_console.print()
    info(f"Configuring server at {ctx.ip}...")
    if domain:
        info(f"Domain: {domain}")
    if sni and sni != DEFAULT_SNI:
        info(f"SNI: {sni}")
    if pq:
        info("Post-quantum encryption: enabled (experimental)")
    if warp:
        info("Cloudflare WARP: enabled")
    if not geo_block:
        info("Geo-blocking: disabled (Russian sites accessible)")
    if not is_quiet_mode():
        err_console.print()

    # Choose pipeline: full setup (panel + node) or node-only
    if is_panel_host:
        steps = build_setup_steps(ctx)
    else:
        steps = build_node_steps(ctx)

    provisioner = Provisioner(steps)

    if remote_executor is None and not isinstance(resolved.conn, ServerConnection):
        fail("No SSH connection available", hint_type="bug")
    remote_executor = remote_executor or SSHRemoteExecutor(resolved.conn)
    conn = RemoteExecutorConnection(remote_executor)

    results = provisioner.run(conn, ctx, reporter=reporter, operation=operation, render=not is_quiet_mode())

    # Check for failures
    failed = [r for r in results if r.status == "failed"]
    if failed:
        fail(
            "Setup failed",
            hint=f"Step '{failed[0].name}' failed: {failed[0].detail}\nRun: meridian preflight {ctx.ip}",
            hint_type="system",
        )

    if not is_quiet_mode():
        err_console.print()
    ok("All provisioning steps completed")


# ---------------------------------------------------------------------------
# Post-provisioner: Remnawave API configuration
# ---------------------------------------------------------------------------


def panel_base_url(ip: str, domain: str, secret_path: str) -> str:
    """Build the panel base URL for API calls.

    Panel runs on 127.0.0.1:3000, reverse-proxied by nginx on the secret path.
    For first deploy before nginx is ready, we use SSH tunnel or direct access.
    Once nginx is up, we use https://<host>/<secret_path>.
    """
    host = domain or ip
    return f"https://{host}/{secret_path}/"


def create_api_token(base_url: str, auth_token: str) -> str:
    """Create a long-lived API token using the admin auth token.

    Remnawave's auth tokens (from login/register) are short-lived browser
    session tokens. API endpoints require a separate API token created via
    POST /api/tokens with the 'remnawave-client-type: browser' header.
    The API token is effectively permanent (~274 years).
    """
    import httpx

    resp = httpx.post(
        f"{base_url.rstrip('/')}/api/tokens",
        json={"tokenName": "meridian-provisioner"},
        headers={
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
            "X-Remnawave-Client-Type": "browser",
        },
        timeout=30,
        verify=False,
    )
    if resp.status_code not in (200, 201):
        fail(
            f"Could not create API token ({resp.status_code}): {resp.text[:200]}",
            hint="The panel may need a fresh start",
            hint_type="system",
        )
    data = resp.json()
    if isinstance(data, dict) and "response" in data:
        data = data["response"]
    token = data.get("token", "")
    if not token:
        fail("API token creation succeeded but no token returned", hint_type="bug")
    return token


def get_docker_gateway(conn: ServerConnection) -> str:
    """Get the Docker gateway IP for panel-to-node communication.

    When panel (bridge network) and node (host network) are on the same server,
    the panel cannot reach 127.0.0.1 on the host. It must use the gateway IP
    of its Docker network to reach services on the host network.

    We inspect the panel container's actual network, not the default bridge,
    because the panel runs on a custom 'remnawave-net' network.
    """
    result = conn.run(
        "docker inspect remnawave --format '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}'",
        timeout=15,
    )
    gateway = result.stdout.strip() if result.returncode == 0 else ""
    return gateway or "172.17.0.1"


def wait_for_panel_api(base_url: str, retries: int = 20, delay: float = 3.0) -> bool:
    """Wait for the panel REST API to become reachable from the deployer."""
    import httpx

    for attempt in range(retries):
        try:
            resp = httpx.get(
                f"{base_url.rstrip('/')}/api/auth/login",
                timeout=10,
                verify=False,  # Self-signed cert during bootstrap
            )
            # Any response (even 405 Method Not Allowed) means the API is up
            if resp.status_code < 500:
                return True
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        if attempt < retries - 1:
            time.sleep(delay)
    return False


# ---------------------------------------------------------------------------
# Panel configuration workflows
# ---------------------------------------------------------------------------


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
    pq: bool = False,
    warp: bool = False,
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

    if not is_quiet_mode():
        err_console.print()
    info("Configuring panel via API...")

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
            pq=pq,
            warp=warp,
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
            pq=pq,
            warp=warp,
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
    pq: bool,
    warp: bool = False,
    geo_block: bool,
    version: str,
    xhttp_path: str = "",
    ws_path: str = "",
    info_page_path: str = "",
) -> None:
    """First deploy: full panel bootstrap from scratch."""
    base_url = panel_base_url(resolved.ip, domain, secret_path)

    # Wait for panel API to become accessible
    info("Waiting for panel API...")
    if not wait_for_panel_api(base_url):
        fail(
            "Panel API is not reachable",
            hint=(
                f"Panel should be at {base_url}\n"
                f"Check: ssh {resolved.user}@{resolved.ip} docker logs remnawave --tail 30"
            ),
            hint_type="system",
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
            fail(
                f"Panel admin registration failed ({e}), login also failed ({login_err})",
                hint="Panel may need a fresh start: meridian teardown, then redeploy",
                hint_type="system",
            )
    ok("Panel admin registered")

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
    ok("API token created")

    # Update cluster with the API token
    cluster.panel.api_token = api_token
    cluster.save()

    # Configure subscription page with the real API token
    from meridian.provision.remnawave_panel import configure_subscription_page

    if configure_subscription_page(resolved.conn, api_token):
        from meridian.cluster import SubscriptionPageConfig

        if cluster.subscription_page is None:
            cluster.subscription_page = SubscriptionPageConfig()
        cluster.subscription_page.deployed = True
        cluster.save()
    ok("Subscription page configured")

    with MeridianPanel(base_url, api_token) as panel:
        # Create config profile (Xray inbound definitions)
        profile_name = "meridian-default"
        xray_result = build_xray_config(
            resolved.conn,
            sni=sni,
            reality_port=reality_port,
            xhttp_port=xhttp_port,
            wss_port=wss_port,
            domain=domain,
            pq=pq,
            geo_block=geo_block,
            warp=warp,
            xhttp_path=xhttp_path,
            ws_path=ws_path,
        )
        xray_config = xray_result.config
        reality_public_key = xray_result.reality_public_key
        reality_short_id = xray_result.reality_short_id
        reality_private_key = xray_result.reality_private_key

        try:
            existing_profile = panel.find_config_profile_by_name(profile_name)
            if existing_profile:
                profile = existing_profile
                info(f"Config profile '{profile_name}' already exists, reusing")
            else:
                profile = panel.create_config_profile(profile_name, xray_config)
                ok("Config profile created")
            cluster.config_profile_uuid = profile.uuid
            cluster.config_profile_name = profile.name
        except RemnawaveError as e:
            fail(f"Failed to create config profile: {e}", hint_type="system")

        # Cache inbound references from the profile
        cache_inbounds(panel, cluster)

        # Assign inbounds to Default-Squad so users can access them.
        # Remnawave requires users and inbounds to share an "internal squad"
        # for the user to appear in the node's Xray config.
        try:
            squad_uuid = select_default_squad_uuid(panel.list_internal_squads())
            if squad_uuid:
                inbound_uuids_all = [
                    ref.uuid for ref in cluster.inbounds.values() if isinstance(ref, InboundRef) and ref.uuid
                ]
                if inbound_uuids_all:
                    panel.assign_inbounds_to_squad(squad_uuid, inbound_uuids_all)
                    ok("Inbounds linked to Default-Squad")
                cluster.squad_uuid = squad_uuid
            else:
                warn("Default-Squad not found — users may not get access to inbounds")
        except RemnawaveError as e:
            warn(f"Could not configure squad: {e}")

        # Register this server as a node
        # For same-server deployments (panel + node co-located), the panel runs
        # in a Docker bridge network and cannot reach the node on 127.0.0.1.
        # Use the Docker bridge gateway IP so the panel can reach the node.
        node_address = get_docker_gateway(resolved.conn)
        node_name = domain or resolved.ip
        try:
            inbound_uuids = [ref.uuid for ref in cluster.inbounds.values() if isinstance(ref, InboundRef) and ref.uuid]
            existing_api_node = panel.find_node_by_address(node_address)
            if existing_api_node:
                info(f"Node at {node_address} already registered, reusing")
                # Re-fetch keygen for secret key (needed for container .env)
                secret_key = panel.get_node_secret_key()
                node_creds = NodeCredentials(uuid=existing_api_node.uuid, secret_key=secret_key)
            else:
                node_creds = panel.create_node(
                    name=node_name,
                    address=node_address,
                    port=REMNAWAVE_NODE_API_PORT,
                    config_profile_uuid=cluster.config_profile_uuid,
                    inbound_uuids=inbound_uuids,
                )
                ok(f"Node registered: {node_name}")
        except RemnawaveError as e:
            fail(f"Failed to register node: {e}", hint_type="system")

        if not deploy_node_container(resolved.conn, node_creds.secret_key):
            fail(
                "Node container did not become healthy",
                hint=(f"Check: ssh {shlex.quote(resolved.user)}@{resolved.ip} docker logs remnawave-node --tail 50"),
                hint_type="system",
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
        create_hosts_for_node(panel, cluster, resolved.ip, domain, sni, reality_port)

        # Create first client
        try:
            existing_user = panel.get_user(client_name)
            if existing_user:
                info(f"Client '{client_name}' already exists")
                user = existing_user
            else:
                squad_uuids = [cluster.squad_uuid] if cluster.squad_uuid else None
                user = panel.create_user(client_name, squad_uuids=squad_uuids)
                ok(f"Client '{client_name}' created")

            # Deploy connection page for this client
            if user and isinstance(getattr(user, "vless_uuid", None), str) and user.vless_uuid:
                try:
                    sub_url = panel.get_subscription_url(user.short_uuid) if user.short_uuid else ""
                    page_url = deploy_client_page(
                        resolved.conn, cluster, node_entry, user.vless_uuid, client_name, sub_url
                    )
                    if page_url:
                        ok("Connection page deployed")
                        cluster._extra["_page_url"] = page_url
                    if sub_url:
                        cluster._extra["_subscription_url"] = sub_url
                except (OSError, RuntimeError):
                    pass  # Non-fatal — subscription URL still works
        except RemnawaveError as e:
            warn(f"Could not create client '{client_name}': {e}")

    ok("Panel configuration complete")


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
    pq: bool = False,
    warp: bool = False,
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
        fail(
            "Cluster config exists but has no API token",
            hint="Run: meridian teardown, then redeploy from scratch",
            hint_type="system",
        )

    node = cluster.find_node(resolved.ip)
    if not node:
        fail(f"Node {resolved.ip} not found in cluster config", hint_type="system")

    try:
        with MeridianPanel(cluster.panel.url, cluster.panel.api_token) as panel:
            if not panel.ping():
                fail(
                    "Cannot reach panel API",
                    hint=f"Panel URL: {cluster.panel.url}\nCheck panel logs on {cluster.panel.server_ip}",
                    hint_type="system",
                )
            ok("Panel API accessible")

            # Build Xray config reusing existing Reality keys
            # If we have saved keys, skip keygen (preserves client configs)
            if node.reality_private_key and node.reality_public_key and node.reality_short_id:
                info("Reusing existing Reality keys (client configs preserved)")
                xray_result = build_xray_config(
                    None,  # no SSH needed when reusing keys
                    sni=sni or node.sni,
                    reality_port=reality_port,
                    xhttp_port=xhttp_port,
                    wss_port=wss_port,
                    domain=domain or node.domain,
                    pq=pq,
                    geo_block=geo_block,
                    warp=warp,
                    xhttp_path=xhttp_path or node.xhttp_path,
                    ws_path=ws_path or node.ws_path,
                    existing_private_key=node.reality_private_key,
                    existing_public_key=node.reality_public_key,
                    existing_short_id=node.reality_short_id,
                )
            else:
                # No saved keys — must regenerate (breaks existing client configs)
                warn("No saved Reality keys — regenerating (clients will need new configs)")
                xray_result = build_xray_config(
                    resolved.conn,
                    sni=sni or node.sni,
                    reality_port=reality_port,
                    xhttp_port=xhttp_port,
                    wss_port=wss_port,
                    domain=domain or node.domain,
                    pq=pq,
                    geo_block=geo_block,
                    warp=warp,
                    xhttp_path=xhttp_path or node.xhttp_path,
                    ws_path=ws_path or node.ws_path,
                )

            xray_config = xray_result.config
            reality_public_key = xray_result.reality_public_key
            reality_short_id = xray_result.reality_short_id
            reality_private_key = xray_result.reality_private_key

            # Update or create config profile
            profile_name = cluster.config_profile_name or "meridian-default"
            try:
                existing_profile = panel.find_config_profile_by_name(profile_name)
                if existing_profile:
                    profile = existing_profile
                    info(f"Config profile '{profile_name}' already exists, reusing")
                else:
                    profile = panel.create_config_profile(profile_name, xray_config)
                    ok("Config profile created")
                cluster.config_profile_uuid = profile.uuid
                cluster.config_profile_name = profile.name
            except RemnawaveError as e:
                warn(f"Could not update config profile: {e}")

            # Re-cache inbounds
            cache_inbounds(panel, cluster)

            # Refresh squad-inbound linkage
            try:
                squad_uuid = cluster.squad_uuid or select_default_squad_uuid(panel.list_internal_squads())
                if squad_uuid:
                    inbound_uuids_all = [
                        ref.uuid for ref in cluster.inbounds.values() if isinstance(ref, InboundRef) and ref.uuid
                    ]
                    if inbound_uuids_all:
                        panel.assign_inbounds_to_squad(squad_uuid, inbound_uuids_all)
                    cluster.squad_uuid = squad_uuid
            except RemnawaveError:
                pass  # Non-fatal on redeploy

            # Verify node registration
            if node.uuid:
                api_node = panel.get_node(node.uuid)
                if api_node:
                    ok(f"Node {resolved.ip} still registered")
                else:
                    warn(f"Node {resolved.ip} not found in panel — re-registering")
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
                        fail(
                            "Node container did not become healthy",
                            hint=(
                                f"Check: ssh {shlex.quote(resolved.user)}@{resolved.ip} "
                                "docker logs remnawave-node --tail 50"
                            ),
                            hint_type="system",
                        )
            else:
                warn("Node has no UUID — skipping panel verification")

            # Redeploy node container (refresh secret key + image)
            if node.uuid:
                secret_key = panel.get_node_secret_key()
                if secret_key:
                    if not deploy_node_container(resolved.conn, secret_key):
                        fail(
                            "Node container did not become healthy",
                            hint=(
                                f"Check: ssh {shlex.quote(resolved.user)}@{resolved.ip} "
                                "docker logs remnawave-node --tail 50"
                            ),
                            hint_type="system",
                        )

            # Recreate hosts (idempotent)
            # Use provided values; only fall back to stored values if caller passed ""
            # (which means "not specified" from imperative commands, or "clear" from apply)
            effective_domain = domain if domain else node.domain
            effective_sni = sni if sni else node.sni
            create_hosts_for_node(panel, cluster, resolved.ip, effective_domain, effective_sni, reality_port)

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
            ok("Node configuration updated")

            # Ensure subscription page has a valid token (upgrade from pre-subscription deploys)
            if node.is_panel_host:
                from meridian.provision.remnawave_panel import configure_subscription_page

                if configure_subscription_page(resolved.conn, cluster.panel.api_token):
                    from meridian.cluster import SubscriptionPageConfig

                    if cluster.subscription_page is None:
                        cluster.subscription_page = SubscriptionPageConfig()
                    cluster.subscription_page.deployed = True
                    cluster.save()

    except RemnawaveError as e:
        fail(f"Panel API error: {e}", hint_type="system")


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
        fail(
            "No panel configured -- deploy a panel first with: meridian deploy <IP>",
            hint_type="user",
        )

    try:
        with MeridianPanel(cluster.panel.url, cluster.panel.api_token) as panel:
            if not panel.ping():
                fail(
                    "Cannot reach panel API",
                    hint=f"Panel URL: {cluster.panel.url}\nCheck panel logs on {cluster.panel.server_ip}",
                    hint_type="system",
                )

            node_name = domain or resolved.ip
            inbound_uuids = [ref.uuid for ref in cluster.inbounds.values() if isinstance(ref, InboundRef) and ref.uuid]
            existing_api_node = panel.find_node_by_address(resolved.ip)
            if existing_api_node:
                info(f"Node at {resolved.ip} already registered, reusing")
                secret_key = panel.get_node_secret_key()
                node_creds = NodeCredentials(uuid=existing_api_node.uuid, secret_key=secret_key)
            else:
                node_creds = panel.create_node(
                    name=node_name,
                    address=resolved.ip,
                    port=REMNAWAVE_NODE_API_PORT,
                    config_profile_uuid=cluster.config_profile_uuid,
                    inbound_uuids=inbound_uuids,
                )
                ok(f"Node registered: {node_name}")

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
            create_hosts_for_node(panel, cluster, resolved.ip, domain, sni, reality_port)

    except RemnawaveError as e:
        fail(f"Panel API error: {e}", hint_type="system")

    ok("New node configured")


# ---------------------------------------------------------------------------
# Panel API helpers
# ---------------------------------------------------------------------------


def select_default_squad_uuid(squads: list[dict[str, Any]]) -> str:
    """Select the Default-Squad UUID from a list of squads.

    Policy: prefer the squad named "Default-Squad"; fall back to the first
    available squad. Panel v2.7+ may not auto-create "Default-Squad".
    """
    for s in squads:
        if isinstance(s, dict) and s.get("name") == "Default-Squad":
            return str(s.get("uuid", ""))
    if squads and isinstance(squads[0], dict):
        return str(squads[0].get("uuid", ""))
    return ""


def cache_inbounds(panel: MeridianPanel, cluster: ClusterConfig) -> None:
    """Fetch inbound definitions from the panel and cache their UUIDs."""
    try:
        inbounds = panel.list_inbounds()
        tag_map = {
            "vless-reality": ProtocolKey.REALITY,
            "vless-xhttp": ProtocolKey.XHTTP,
            "vless-wss": ProtocolKey.WSS,
        }
        for ib in inbounds:
            key = tag_map.get(ib.tag)
            if key:
                cluster.inbounds[str(key)] = InboundRef(uuid=ib.uuid, tag=ib.tag)
        ok(f"Cached {len(cluster.inbounds)} inbound references")
    except RemnawaveError as e:
        warn(f"Could not cache inbound references: {e}")


def create_hosts_for_node(
    panel: MeridianPanel,
    cluster: ClusterConfig,
    node_ip: str,
    domain: str,
    sni: str,
    reality_port: int,
) -> None:
    """Create direct host entries for a node's protocols."""
    host_address = domain or node_ip

    # Batch-fetch all hosts once instead of per-protocol find_host_by_remark
    try:
        existing_remarks = {h.remark for h in panel.list_hosts()}
    except RemnawaveError:
        existing_remarks = set()

    # Reality host (direct IP, port 443 or computed)
    reality_ref = cluster.get_inbound(ProtocolKey.REALITY)
    if reality_ref and reality_ref.uuid:
        remark = f"reality-{node_ip}"
        if remark in existing_remarks:
            info(f"Host '{remark}' already exists, skipping")
        else:
            try:
                panel.create_host(
                    remark=remark,
                    address=node_ip,
                    port=reality_port,
                    config_profile_uuid=cluster.config_profile_uuid,
                    inbound_uuid=reality_ref.uuid,
                    sni=sni,
                    fingerprint="chrome",
                    security_layer="DEFAULT",
                )
                ok(f"Host created: Reality via {node_ip}:{reality_port}")
            except RemnawaveError as e:
                warn(f"Could not create Reality host: {e}")

    # XHTTP host (via domain or IP, port 443 through nginx)
    xhttp_ref = cluster.get_inbound(ProtocolKey.XHTTP)
    if xhttp_ref and xhttp_ref.uuid:
        remark = f"xhttp-{host_address}"
        if remark in existing_remarks:
            info(f"Host '{remark}' already exists, skipping")
        else:
            try:
                panel.create_host(
                    remark=remark,
                    address=host_address,
                    port=443,
                    config_profile_uuid=cluster.config_profile_uuid,
                    inbound_uuid=xhttp_ref.uuid,
                    security_layer="TLS",
                )
                ok(f"Host created: XHTTP via {host_address}:443")
            except RemnawaveError as e:
                warn(f"Could not create XHTTP host: {e}")

    # WSS host (domain mode only, port 443 through CDN)
    if domain:
        wss_ref = cluster.get_inbound(ProtocolKey.WSS)
        if wss_ref and wss_ref.uuid:
            remark = f"wss-{domain}"
            if remark in existing_remarks:
                info(f"Host '{remark}' already exists, skipping")
            else:
                try:
                    panel.create_host(
                        remark=remark,
                        address=domain,
                        port=443,
                        config_profile_uuid=cluster.config_profile_uuid,
                        inbound_uuid=wss_ref.uuid,
                        security_layer="TLS",
                    )
                    ok(f"Host created: WSS via {domain}:443")
                except RemnawaveError as e:
                    warn(f"Could not create WSS host: {e}")


def deploy_node_container(conn: ServerConnection, secret_key: str) -> bool:
    """Deploy the Remnawave node container with the given secret key.

    Creates /opt/remnanode, writes docker-compose.yml and .env, pulls the
    image, and starts the container. Called from the post-provisioner phase
    after the panel API has registered the node and returned the mTLS secret.
    """
    from meridian.config import REMNAWAVE_NODE_API_PORT, REMNAWAVE_NODE_DIR, REMNAWAVE_NODE_IMAGE
    from meridian.provision.remnawave_node import _render_node_compose, _render_node_env

    node_dir = REMNAWAVE_NODE_DIR
    q_dir = shlex.quote(node_dir)

    # Create directory
    result = conn.run(f"mkdir -p {q_dir} && chmod 700 {q_dir}", timeout=15)
    if result.returncode != 0:
        warn(f"Could not create {node_dir}: {result.stderr.strip()[:200]}")
        return False

    # Write .env
    env_content = _render_node_env(REMNAWAVE_NODE_API_PORT, secret_key)
    env_path = f"{node_dir}/.env"
    result = conn.put_text(
        env_path,
        env_content,
        mode="600",
        sensitive=True,
        timeout=15,
        operation_name="write remnawave node env",
    )
    if result.returncode != 0:
        warn(f"Could not write {env_path}: {result.stderr.strip()[:200]}")
        return False

    # Write docker-compose.yml
    compose_content = _render_node_compose(REMNAWAVE_NODE_IMAGE, REMNAWAVE_NODE_API_PORT)
    compose_path = f"{node_dir}/docker-compose.yml"
    result = conn.put_text(
        compose_path,
        compose_content,
        mode="644",
        timeout=15,
        operation_name="write remnawave node compose",
    )
    if result.returncode != 0:
        warn(f"Could not write {compose_path}: {result.stderr.strip()[:200]}")
        return False

    # Pull image
    info("Pulling Remnawave node image...")
    result = conn.run(
        "docker compose pull",
        cwd=node_dir,
        timeout=300,
        retries=3,
        retry_delay=10,
        operation_name="pull remnawave node image",
    )
    if result.returncode != 0:
        warn("Could not pull node image — node may not start")
        return False

    # Start container
    result = conn.run("docker compose up -d", cwd=node_dir, timeout=120)
    if result.returncode != 0:
        warn(f"Node container failed to start: {result.stderr.strip()[:200]}")
        return False

    ok("Remnawave node deployed")

    # Health gate: verify the node container started
    info("Verifying node container health...")
    node_healthy = False
    for _attempt in range(10):
        check = conn.run(
            "docker inspect remnawave-node --format '{{.State.Running}}' 2>/dev/null",
            timeout=10,
        )
        if check.returncode == 0 and "true" in check.stdout.strip().lower():
            node_healthy = True
            break
        time.sleep(3)

    if node_healthy:
        ok("Node container verified healthy")
    else:
        logs = conn.run("docker logs remnawave-node --tail 20 2>&1", timeout=15)
        log_tail = logs.stdout.strip()[:500] if logs.returncode == 0 else "(no logs available)"
        warn(
            f"Node container may not be healthy after 30s\n"
            f"  Recent logs:\n{log_tail}\n"
            f"  Check: ssh {shlex.quote(conn.user)}@{conn.ip} docker logs remnawave-node"
        )

    # Allow Docker internal traffic to reach the node API port
    # (panel in bridge network → node on host via gateway IP)
    from meridian.config import REMNAWAVE_NODE_API_PORT

    conn.run(
        f"ufw allow from 172.16.0.0/12 to any port {REMNAWAVE_NODE_API_PORT} proto tcp"
        f" comment 'Meridian node API (Docker internal)' 2>/dev/null; true",
        timeout=15,
    )

    return node_healthy


# ---------------------------------------------------------------------------
# Connection page deployment
# ---------------------------------------------------------------------------


def deploy_client_page(
    conn: ServerConnection,
    cluster: ClusterConfig,
    node: NodeEntry,
    user_uuid: str,
    client_name: str,
    sub_url: str = "",
) -> str:
    """Generate and upload a PWA connection page for a client.

    Builds VLESS protocol URLs from cluster.yml node data + client UUID,
    generates QR codes and PWA files, uploads to the server.

    Returns the page URL on success, empty string on failure.
    """
    from meridian.models import ProtocolURL
    from meridian.protocols import PROTOCOLS
    from meridian.pwa import generate_client_files, upload_client_files
    from meridian.urls import generate_qr_base64

    host = node.domain or node.ip
    info_page_path = cluster.panel.sub_path or ""
    if not info_page_path:
        return ""

    page_url = f"https://{host}/{info_page_path}/{user_uuid}/"

    # Build protocol URLs using the protocol registry's build_url() methods
    protocol_urls: list[ProtocolURL] = []

    # Reality (always)
    if node.reality_public_key:
        reality = PROTOCOLS.get("reality")
        if reality:
            url = reality.build_url(
                user_uuid,
                client_name,
                ip=node.ip,
                sni=node.sni,
                public_key=node.reality_public_key,
                short_id=node.reality_short_id or "",
                server_name=cluster.branding.server_name,
            )
            qr = generate_qr_base64(url)
            protocol_urls.append(ProtocolURL(key="reality", label=reality.display_label, url=url, qr_b64=qr))

    # XHTTP (if path configured)
    if node.xhttp_path:
        xhttp = PROTOCOLS.get("xhttp")
        if xhttp:
            url = xhttp.build_url(
                user_uuid,
                client_name,
                ip=node.ip,
                xhttp_path=node.xhttp_path,
                domain=node.domain or "",
                server_name=cluster.branding.server_name,
            )
            qr = generate_qr_base64(url)
            protocol_urls.append(ProtocolURL(key="xhttp", label=xhttp.display_label, url=url, qr_b64=qr))

    # WSS (only in domain mode)
    if node.domain and node.ws_path:
        wss = PROTOCOLS.get("wss")
        if wss:
            url = wss.build_url(
                user_uuid,
                client_name,
                domain=node.domain,
                ws_path=node.ws_path,
                server_name=cluster.branding.server_name,
            )
            qr = generate_qr_base64(url)
            protocol_urls.append(ProtocolURL(key="wss", label=wss.display_label, url=url, qr_b64=qr))

    if not protocol_urls:
        return ""

    files = generate_client_files(
        protocol_urls,
        server_ip=node.ip,
        domain=node.domain or "",
        client_name=client_name,
        server_name=cluster.branding.server_name,
        server_icon=cluster.branding.icon,
        color=cluster.branding.color,
        page_url=page_url,
    )

    error = upload_client_files(conn, user_uuid, files)
    if error:
        warn(f"Could not deploy connection page: {error}")
        return ""

    return page_url
