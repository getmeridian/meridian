"""Cluster recovery -- reconstruct cluster.yml from a running panel.

When local state is lost (new machine, accidental deletion), this command
rebuilds cluster.yml by querying the Remnawave panel API. The panel
database is the source of truth for nodes, inbounds, and config profiles.
"""

from __future__ import annotations

from typing import Any

from meridian.cluster import (
    ClusterConfig,
    InboundRef,
    NodeEntry,
    PanelConfig,
)
from meridian.console import err_console, fail, info, ok, warn
from meridian.remnawave import MeridianPanel, RemnawaveError

# -- Config metadata extraction --


def _extract_config_metadata(profile_raw: dict[str, Any]) -> dict[str, str]:
    """Extract Reality/XHTTP/WSS keys from a config profile's raw API data.

    Parses the Xray config embedded in the profile to recover private keys,
    short IDs, SNI targets, and protocol paths. These are essential for
    preserving existing client configs across recovery + redeploy.

    Returns private/public keys, short ID, SNI, and transport paths.
    Missing values default to empty string.
    """
    result: dict[str, str] = {
        "private_key": "",
        "public_key": "",
        "short_id": "",
        "sni": "",
        "xhttp_path": "",
        "ws_path": "",
    }

    if not isinstance(profile_raw, dict):
        return result

    config = profile_raw.get("config")
    if not isinstance(config, dict):
        return result

    inbounds = config.get("inbounds")
    if not isinstance(inbounds, list):
        return result

    # Index inbounds by tag for direct lookup
    by_tag: dict[str, dict[str, Any]] = {}
    for ib in inbounds:
        if isinstance(ib, dict) and ib.get("tag"):
            by_tag[ib["tag"]] = ib

    # Reality inbound → private_key, short_id, sni
    reality = by_tag.get("vless-reality")
    if reality:
        stream = reality.get("streamSettings")
        if isinstance(stream, dict):
            rs = stream.get("realitySettings")
            if isinstance(rs, dict):
                result["private_key"] = rs.get("privateKey", "") or ""
                result["public_key"] = rs.get("publicKey", "") or ""
                short_ids = rs.get("shortIds")
                if isinstance(short_ids, list) and short_ids:
                    result["short_id"] = str(short_ids[0])
                server_names = rs.get("serverNames")
                if isinstance(server_names, list) and server_names:
                    result["sni"] = str(server_names[0])

    # XHTTP inbound → xhttp_path
    xhttp = by_tag.get("vless-xhttp")
    if xhttp:
        stream = xhttp.get("streamSettings")
        if isinstance(stream, dict):
            xs = stream.get("xhttpSettings")
            if isinstance(xs, dict):
                path = xs.get("path", "") or ""
                result["xhttp_path"] = path.lstrip("/")

    # WSS inbound → ws_path
    wss = by_tag.get("vless-wss")
    if wss:
        stream = wss.get("streamSettings")
        if isinstance(stream, dict):
            ws = stream.get("wsSettings")
            if isinstance(ws, dict):
                path = ws.get("path", "") or ""
                result["ws_path"] = path.lstrip("/")

    return result


# -- Recovery --


def run_recover(panel_url: str, api_token: str) -> None:
    """Reconstruct cluster.yml from a running Remnawave panel."""
    if not panel_url:
        fail(
            "Panel URL is required",
            hint="Usage: meridian recover https://panel.example.com TOKEN",
            hint_type="user",
        )
    if not api_token:
        fail(
            "API token is required",
            hint="Usage: meridian recover PANEL_URL TOKEN",
            hint_type="user",
        )

    # Normalize URL
    panel_url = panel_url.rstrip("/")

    info(f"Connecting to panel at {panel_url}...")

    panel = MeridianPanel(panel_url, api_token)
    with panel:
        # Verify connectivity
        if not panel.ping():
            fail(
                f"Cannot reach panel at {panel_url}",
                hint="Check the URL and API token. Try: curl " + panel_url + "/api/health",
                hint_type="user",
            )
        ok("Panel is reachable")

        # Fetch nodes
        try:
            api_nodes = panel.list_nodes()
        except RemnawaveError as e:
            fail(f"Could not fetch nodes: {e}", hint=e.hint, hint_type=e.category)

        info(f"Found {len(api_nodes)} node(s)")

        # Fetch config profiles
        config_profile_uuid = ""
        config_profile_name = ""
        metadata: dict[str, str] = {}
        try:
            profiles = panel.list_config_profiles()
        except RemnawaveError as exc:
            fail(
                f"Could not fetch config profiles: {exc}",
                hint="Recovery stopped before writing cluster.yml; restore panel API access and retry.",
                hint_type=exc.category,
            )
        if not profiles:
            fail(
                "Panel has no config profile to recover",
                hint="Recovery stopped before writing cluster.yml; repair the panel configuration first.",
                hint_type="system",
            )

        # Use the first profile as the active one.
        config_profile_uuid = profiles[0].uuid
        config_profile_name = profiles[0].name
        info(f"Config profile: {config_profile_name}")
        metadata = _extract_config_metadata(profiles[0]._raw)
        missing_key_fields = [field for field in ("private_key", "short_id", "sni") if not metadata.get(field)]
        if missing_key_fields:
            fail(
                "Config profile is missing recoverable Reality key material",
                hint=(
                    "Recovery stopped before writing cluster.yml; refusing to create state that could rotate "
                    f"client credentials. Missing: {', '.join(missing_key_fields)}"
                ),
                hint_type="system",
            )
        ok("Reality private key recovered from config profile")
        if metadata.get("xhttp_path"):
            info(f"XHTTP path recovered: {metadata['xhttp_path']}")
        if metadata.get("ws_path"):
            info(f"WS path recovered: {metadata['ws_path']}")

        # Fetch inbounds
        inbounds: dict[str, InboundRef] = {}
        try:
            from meridian.node_deploy import inbound_protocol_key

            api_inbounds = panel.list_inbounds()
            for ib in api_inbounds:
                key = inbound_protocol_key(ib.tag)
                if key:
                    inbounds[str(key)] = InboundRef(uuid=ib.uuid, tag=ib.tag)
            info(f"Found {len(inbounds)} inbound(s)")
        except RemnawaveError:
            warn("Could not fetch inbounds")

    # Reconstruct nodes
    nodes: list[NodeEntry] = []
    for i, api_node in enumerate(api_nodes):
        node = NodeEntry(
            ip=api_node.address,
            uuid=api_node.uuid,
            name=api_node.name,
            is_panel_host=(i == 0),  # assume first node is panel host
            sni=metadata.get("sni", ""),
            reality_public_key=metadata.get("public_key", ""),
            reality_private_key=metadata.get("private_key", ""),
            reality_short_id=metadata.get("short_id", ""),
            xhttp_path=metadata.get("xhttp_path", ""),
            ws_path=metadata.get("ws_path", ""),
        )
        nodes.append(node)

    # Build cluster config
    panel_config = PanelConfig(
        url=panel_url,
        api_token=api_token,
    )
    # Set panel server_ip from first node (panel host)
    if nodes and nodes[0].is_panel_host:
        panel_config.server_ip = nodes[0].ip
        # Try to recover sub_path from server (not stored in panel API)
        try:
            from meridian.ssh import ServerConnection
            from meridian.xray_config import derive_reality_public_key

            with ServerConnection(nodes[0].ip, user=nodes[0].ssh_user, port=nodes[0].ssh_port) as conn:
                if metadata.get("private_key") and not metadata.get("public_key"):
                    metadata["public_key"] = derive_reality_public_key(conn, metadata["private_key"])
                    for node in nodes:
                        node.reality_public_key = metadata["public_key"]
                result = conn.run("cat /etc/meridian/sub_path 2>/dev/null", timeout=10)
                if result.returncode == 0 and result.stdout.strip():
                    panel_config.sub_path = result.stdout.strip()
        except (OSError, RuntimeError):
            pass  # Non-fatal — sub_path can be re-generated on next deploy

    if metadata.get("private_key") and not metadata.get("public_key"):
        warn("Reality public key could not be derived; redeploy is blocked until key material is restored")

    cluster = ClusterConfig(
        panel=panel_config,
        config_profile_uuid=config_profile_uuid,
        config_profile_name=config_profile_name,
        nodes=nodes,
        inbounds=inbounds,
    )

    cluster.save()
    ok("Cluster config saved to ~/.meridian/cluster.yml")

    # Print summary
    err_console.print()
    err_console.print("  [bold]Recovered cluster[/bold]")
    err_console.print(f"    Panel:     {panel_url}")
    err_console.print(f"    Nodes:     {len(nodes)}")
    for node in nodes:
        role = " (panel)" if node.is_panel_host else ""
        err_console.print(f"      {node.ip}  {node.name}{role}")
    err_console.print(f"    Inbounds:  {len(inbounds)}")
    if config_profile_name:
        err_console.print(f"    Profile:   {config_profile_name}")
    if metadata.get("sni"):
        err_console.print(f"    SNI:       {metadata['sni']}")

    err_console.print()
    warn("Review ~/.meridian/cluster.yml -- some fields (SSH user, relays) need manual setup")
    err_console.print()
    err_console.print("  [dim]Fleet status:   meridian fleet status[/dim]")
    err_console.print("  [dim]List nodes:     meridian node list[/dim]")
    err_console.print("  [dim]List clients:   meridian client list[/dim]")
    err_console.print()
