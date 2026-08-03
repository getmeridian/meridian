"""Cluster recovery -- reconstruct cluster.yml from a running panel.

When local state is lost (new machine, accidental deletion), this command
rebuilds cluster.yml by querying the Remnawave panel API. The panel
database is the source of truth for nodes, inbounds, and config profiles.
"""

from __future__ import annotations

import getpass
import hmac
import ipaddress
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from rich.markup import escape

from meridian.cluster import (
    ClusterConfig,
    InboundRef,
    NodeEntry,
    PanelConfig,
)
from meridian.console import err_console, fail, info, ok, warn
from meridian.core.errors import LocalStateError
from meridian.core.fleet import public_url
from meridian.core.inputs import (
    validate_hostname_value,
    validate_optional_transport_path_value,
    validate_server_title_value,
    validate_ssh_user_value,
)
from meridian.remnawave import ConfigProfile, InternalSquad, MeridianPanel, Node, RemnawaveAuthError, RemnawaveError
from meridian.ssh import SSHError

_V4_PROFILE_PREFIX = "Meridian v4 "
_REALITY_KEY_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_SHORT_ID_RE = re.compile(r"(?:[0-9a-fA-F]{2}){1,8}")

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
                for source, target in (("privateKey", "private_key"), ("publicKey", "public_key")):
                    value = rs.get(source, "") or ""
                    if not isinstance(value, str):
                        raise ValueError(f"Reality {source} must be a string.")
                    result[target] = value
                short_ids = rs.get("shortIds")
                if isinstance(short_ids, list) and short_ids:
                    if not isinstance(short_ids[0], str):
                        raise ValueError("Reality shortIds must contain strings.")
                    result["short_id"] = short_ids[0]
                server_names = rs.get("serverNames")
                if isinstance(server_names, list) and server_names:
                    if not isinstance(server_names[0], str):
                        raise ValueError("Reality serverNames must contain strings.")
                    result["sni"] = server_names[0]

    # XHTTP inbound → xhttp_path
    xhttp = by_tag.get("vless-xhttp")
    if xhttp:
        stream = xhttp.get("streamSettings")
        if isinstance(stream, dict):
            xs = stream.get("xhttpSettings")
            if isinstance(xs, dict):
                path = xs.get("path", "") or ""
                if not isinstance(path, str):
                    raise ValueError("XHTTP path must be a string.")
                result["xhttp_path"] = path.lstrip("/")

    # WSS inbound → ws_path
    wss = by_tag.get("vless-wss")
    if wss:
        stream = wss.get("streamSettings")
        if isinstance(stream, dict):
            ws = stream.get("wsSettings")
            if isinstance(ws, dict):
                path = ws.get("path", "") or ""
                if not isinstance(path, str):
                    raise ValueError("WebSocket path must be a string.")
                result["ws_path"] = path.lstrip("/")

    return result


def _validate_config_metadata(metadata: dict[str, str]) -> dict[str, str]:
    """Validate panel-derived transport credentials before SSH or persistence."""
    for field in ("private_key", "public_key"):
        value = metadata.get(field, "")
        if value and _REALITY_KEY_RE.fullmatch(value) is None:
            raise ValueError(f"Reality {field.replace('_', ' ')} has an invalid shape.")
    short_id = metadata.get("short_id", "")
    if short_id and _SHORT_ID_RE.fullmatch(short_id) is None:
        raise ValueError("Reality short ID must be 2-16 hexadecimal characters with an even length.")
    if metadata.get("sni"):
        metadata["sni"] = validate_hostname_value(metadata["sni"])
    for field in ("xhttp_path", "ws_path"):
        metadata[field] = validate_optional_transport_path_value(metadata.get(field, ""))
    return metadata


# -- Recovery --


def require_legacy_recovery(legacy: bool) -> None:
    """Require explicit acknowledgement before reading recovery credentials."""
    if not legacy:
        fail(
            "Panel recovery supports legacy shared-profile deployments only",
            hint=(
                "Restore V4 cluster.yml from backup. Pass --legacy only after confirming the panel uses "
                "legacy topology."
            ),
            hint_type="user",
        )


def load_api_token(token_file: Path | None = None) -> str:
    """Load a panel token without requiring it on the process command line."""
    env_token = os.environ.get("MERIDIAN_API_TOKEN", "").strip()
    if token_file is not None and env_token:
        fail(
            "Panel token was provided by both MERIDIAN_API_TOKEN and --api-token-file",
            hint="Choose one secret source and retry.",
            hint_type="user",
        )
    if token_file is not None:
        try:
            if token_file.stat().st_mode & 0o077:
                fail(
                    f"Token file {token_file} is readable by other users",
                    hint=f"Restrict it first: chmod 600 {token_file}",
                    hint_type="user",
                )
            token = token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            fail(
                f"Could not read the panel token file: {exc}",
                hint="Check the path and file permissions.",
                hint_type="user",
            )
        if not token:
            fail("Panel token file is empty", hint_type="user")
        return token
    if env_token:
        return env_token
    if sys.stdin.isatty():
        token = getpass.getpass("Remnawave API token: ").strip()
        if token:
            return token
    fail(
        "Remnawave API token is required",
        hint="Set MERIDIAN_API_TOKEN or pass --api-token-file with a mode-600 file.",
        hint_type="user",
    )


def _normalize_panel_url(panel_url: str) -> str:
    """Validate and normalize a secret-path Remnawave API URL."""
    candidate = panel_url.strip()
    try:
        parts = urlsplit(candidate)
        _ = parts.port
    except ValueError:
        fail("Panel URL is invalid", hint="Pass a complete HTTPS panel URL.", hint_type="user")
    if parts.scheme.lower() != "https" or not parts.hostname:
        fail(
            "Panel URL must use HTTPS",
            hint="Pass the complete https:// panel URL, including its secret path.",
            hint_type="user",
        )
    if parts.username or parts.password or parts.query or parts.fragment:
        fail(
            "Panel URL cannot contain credentials, a query, or a fragment",
            hint="Pass only the HTTPS panel base URL and secret path.",
            hint_type="user",
        )
    try:
        secret_path = validate_optional_transport_path_value(parts.path.rstrip("/"))
    except ValueError as exc:
        fail(f"Panel secret path is invalid: {exc}", hint_type="user")
    if not secret_path:
        fail(
            "Panel URL must include its secret path",
            hint="Pass the complete URL used to open the Remnawave panel.",
            hint_type="user",
        )
    return urlunsplit(("https", parts.netloc, f"/{secret_path}", "", ""))


def _select_profile(profiles: list[ConfigProfile], selector: str) -> ConfigProfile:
    """Select one unambiguous legacy config profile."""
    if selector:
        matches = [profile for profile in profiles if selector in {profile.uuid, profile.name}]
        if len(matches) != 1:
            fail(
                f"Config profile '{selector}' was not found",
                hint="Pass an exact profile name or UUID from the panel.",
                hint_type="user",
            )
        selected = matches[0]
    elif len(profiles) == 1:
        selected = profiles[0]
    else:
        fail(
            f"Panel has {len(profiles)} config profiles; recovery is ambiguous",
            hint="Choose the legacy shared profile with --profile NAME_OR_UUID.",
            hint_type="user",
        )
    if selected.name.startswith(_V4_PROFILE_PREFIX):
        fail(
            "V4 topology cannot be recovered from the panel API alone",
            hint="Restore cluster.yml from backup, or run `meridian setup` and review a new V4 plan.",
            hint_type="user",
        )
    return selected


def _profile_nodes(api_nodes: list[Node], profile: ConfigProfile, *, profiles: int) -> list[Node]:
    """Keep only nodes provably assigned to the selected profile."""
    if profiles == 1:
        return api_nodes
    if any(not node.active_config_profile_uuid for node in api_nodes):
        fail(
            "Some panel nodes do not report an active config profile",
            hint="Recovery cannot safely assign nodes while multiple profiles exist.",
            hint_type="system",
        )
    return [node for node in api_nodes if node.active_config_profile_uuid == profile.uuid]


def _select_panel_node(api_nodes: list[Node], selector: str, panel_url: str) -> Node:
    """Identify the legacy node that also hosts the panel."""
    if selector:
        matches = [node for node in api_nodes if selector in {node.uuid, node.name, node.address}]
        if len(matches) != 1:
            fail(
                f"Panel node '{selector}' was not found in the selected profile",
                hint="Pass an exact node name, UUID, or address.",
                hint_type="user",
            )
        return matches[0]
    hostname = urlsplit(panel_url).hostname or ""
    host_matches = [node for node in api_nodes if node.address == hostname]
    if len(host_matches) == 1:
        return host_matches[0]
    if len(api_nodes) == 1:
        return api_nodes[0]
    fail(
        "Panel host cannot be inferred from multiple nodes",
        hint="Pass --panel-node with the node name, UUID, or address that hosts Remnawave.",
        hint_type="user",
    )


def _panel_server_address(panel_url: str, explicit: str) -> str:
    """Resolve the public SSH address without trusting a panel node's Docker address."""
    candidate = explicit.strip()
    if not candidate:
        hostname = urlsplit(panel_url).hostname or ""
        try:
            return str(ipaddress.ip_address(hostname))
        except ValueError:
            fail(
                "Public panel server address cannot be inferred from a domain URL",
                hint="Pass --panel-server with the public SSH IP that hosts Remnawave.",
                hint_type="user",
            )
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        fail(
            "Panel server must be an IP address",
            hint="Pass the public SSH IPv4 or IPv6 address with --panel-server.",
            hint_type="user",
        )


def _select_squad(
    squads: list[InternalSquad],
    inbound_uuids: set[str],
    selector: str,
) -> InternalSquad:
    """Select one squad proven to grant every recovered inbound."""
    eligible = [squad for squad in squads if inbound_uuids.issubset(set(squad.inbound_uuids))]
    if selector:
        matches = [squad for squad in eligible if selector in {squad.uuid, squad.name}]
        if len(matches) != 1:
            fail(
                f"Access squad '{selector}' was not found with all recovered inbounds",
                hint="Pass an exact squad name or UUID that grants the selected profile's inbounds.",
                hint_type="user",
            )
        return matches[0]
    if len(eligible) == 1:
        return eligible[0]
    if not eligible:
        fail(
            "No access squad grants all recovered inbounds",
            hint="Repair the legacy panel squad linkage before retrying recovery.",
            hint_type="system",
        )
    fail(
        f"{len(eligible)} access squads grant the recovered inbounds; recovery is ambiguous",
        hint="Choose the legacy client access squad with --squad NAME_OR_UUID.",
        hint_type="user",
    )


def run_recover(
    panel_url: str,
    api_token: str,
    *,
    profile: str = "",
    squad: str = "",
    panel_node: str = "",
    panel_server: str = "",
    user: str = "root",
    ssh_port: int = 22,
    legacy: bool = False,
    force: bool = False,
) -> None:
    """Reconstruct legacy-compatible cluster.yml from a running Remnawave panel."""
    from meridian.config import CLUSTER_CONFIG

    require_legacy_recovery(legacy)
    if CLUSTER_CONFIG.exists() and not force:
        fail(
            "A local cluster.yml already exists",
            hint="Review the existing state. Pass --force only to back it up and replace it.",
            hint_type="user",
        )
    if not panel_url.strip():
        fail(
            "Panel URL is required",
            hint="Pass --panel-url with the complete HTTPS panel URL.",
            hint_type="user",
        )
    if not api_token:
        fail(
            "API token is required",
            hint="Set MERIDIAN_API_TOKEN or pass --api-token-file.",
            hint_type="user",
        )
    try:
        user = validate_ssh_user_value(user)
    except ValueError as exc:
        fail(str(exc), hint_type="user")
    if not 1 <= ssh_port <= 65535:
        fail("SSH port must be between 1 and 65535", hint_type="user")

    panel_url = _normalize_panel_url(panel_url)
    panel_public_url = public_url(panel_url)

    info(f"Connecting to panel at {panel_public_url}...")

    panel = MeridianPanel(panel_url, api_token)
    with panel:
        # Verify connectivity
        try:
            panel_reachable = panel.ping()
        except RemnawaveAuthError as exc:
            fail(
                "Panel authentication failed",
                hint=exc.hint or "Replace the expired or invalid API token and retry.",
                hint_type="user",
            )
        if not panel_reachable:
            fail(
                f"Cannot reach panel at {panel_public_url}",
                hint="Check the complete panel URL, TLS certificate, and API token.",
                hint_type="system",
            )
        ok("Panel is reachable")

        # Fetch nodes
        try:
            api_nodes = panel.list_nodes()
        except RemnawaveError as e:
            fail(f"Could not fetch nodes: {e}", hint=e.hint, hint_type=e.category)

        info(f"Found {len(api_nodes)} node(s)")

        # Fetch and disambiguate the one legacy shared config profile.
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

        selected_profile = _select_profile(profiles, profile)
        config_profile_uuid = selected_profile.uuid
        config_profile_name = selected_profile.name
        if (
            not config_profile_uuid
            or not config_profile_uuid.isprintable()
            or not config_profile_name
            or not config_profile_name.isprintable()
            or len(config_profile_uuid) > 120
            or len(config_profile_name) > 200
        ):
            fail("Selected profile identity is invalid", hint_type="system")
        api_nodes = _profile_nodes(api_nodes, selected_profile, profiles=len(profiles))
        if not api_nodes:
            fail(
                "The selected profile has no panel nodes",
                hint="Choose a profile assigned to at least one node.",
                hint_type="user",
            )
        selected_panel_node = _select_panel_node(api_nodes, panel_node, panel_url)
        panel_server_ip = _panel_server_address(panel_url, panel_server)
        info(f"Config profile: {config_profile_name}")
        profile_payload = selected_profile._raw
        if not isinstance(profile_payload.get("config"), dict):
            profile_payload = {"config": selected_profile.config}
        try:
            metadata = _validate_config_metadata(_extract_config_metadata(profile_payload))
        except ValueError as exc:
            fail(
                f"Config profile contains invalid recovery metadata: {exc}",
                hint="Repair the panel profile or restore cluster.yml from backup; no local state was written.",
                hint_type="system",
            )
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

        # Fetch inbounds
        inbounds: dict[str, InboundRef] = {}
        try:
            from meridian.node_deploy import inbound_protocol_key

            api_inbounds = panel.list_inbounds()
            for ib in api_inbounds:
                if ib.profile_uuid and ib.profile_uuid != config_profile_uuid:
                    continue
                key = inbound_protocol_key(ib.tag)
                if key:
                    inbounds[str(key)] = InboundRef(uuid=ib.uuid, tag=ib.tag)
        except RemnawaveError as exc:
            fail(
                f"Could not fetch inbounds: {exc}",
                hint="Recovery stopped before writing cluster.yml; restore panel API access and retry.",
                hint_type=exc.category,
            )
        if "reality" not in inbounds:
            fail(
                "The selected profile has no recoverable Reality inbound",
                hint="Choose the legacy Meridian profile that owns the active Reality inbound.",
                hint_type="system",
            )
        info(f"Found {len(inbounds)} inbound(s)")
        try:
            squads = panel.list_internal_squads()
        except RemnawaveError as exc:
            fail(
                f"Could not fetch access squads: {exc}",
                hint="Recovery stopped before writing cluster.yml; restore panel API access and retry.",
                hint_type=exc.category,
            )
        selected_squad = _select_squad(squads, {ref.uuid for ref in inbounds.values()}, squad)

        node_domains: dict[str, str] = {}
        if metadata.get("ws_path"):
            wss_ref = inbounds.get("wss")
            if wss_ref is None:
                fail(
                    "The recovered profile has a WebSocket path but no WSS inbound",
                    hint="Repair the legacy profile before recovery; no local state was written.",
                    hint_type="system",
                )
            try:
                hosts = panel.list_hosts()
            except RemnawaveError as exc:
                fail(
                    f"Could not fetch WSS hosts: {exc}",
                    hint="Recovery cannot safely reconstruct the WSS domain without Host records.",
                    hint_type=exc.category,
                )
            domains: set[str] = set()
            for host in hosts:
                if host.inbound_uuid != wss_ref.uuid:
                    continue
                candidate = host.host or host.sni or host.address
                try:
                    domains.add(validate_hostname_value(candidate))
                except ValueError:
                    continue
            if len(api_nodes) == 1 and len(domains) == 1:
                node_domains[api_nodes[0].uuid] = next(iter(domains))
            else:
                fail(
                    "WSS node-to-domain mapping is ambiguous in panel state",
                    hint=(
                        "Recovery refuses to save a WebSocket path without its node domain. Restore cluster.yml "
                        "from backup or remove the ambiguous WSS transport before retrying."
                    ),
                    hint_type="user",
                )

    secret_path = validate_optional_transport_path_value(urlsplit(panel_url).path)
    panel_config = PanelConfig(
        url=panel_url,
        api_token=api_token,
        server_ip=panel_server_ip,
        ssh_user=user,
        ssh_port=ssh_port,
        secret_path=secret_path,
    )
    # The public key is not present in every panel response. Derive it from the
    # selected panel node, but never save partial Reality key material.
    panel_ssh_validated = False
    try:
        from meridian.ssh import ServerConnection
        from meridian.xray_config import derive_reality_public_key

        with ServerConnection(panel_server_ip, user=user, port=ssh_port) as conn:
            derived_public_key = derive_reality_public_key(conn, metadata["private_key"])
            if derived_public_key:
                if _REALITY_KEY_RE.fullmatch(derived_public_key) is None:
                    fail("Derived Reality public key has an invalid shape", hint_type="system")
                if metadata.get("public_key") and not hmac.compare_digest(metadata["public_key"], derived_public_key):
                    fail(
                        "Recovered Reality public key does not match its private key",
                        hint="Repair the panel profile or restore cluster.yml from backup; no state was written.",
                        hint_type="system",
                    )
                metadata["public_key"] = derived_public_key
            result = conn.run("cat /etc/meridian/sub_path 2>/dev/null", timeout=10)
            panel_ssh_validated = result.returncode != 255
            if result.returncode == 0 and result.stdout.strip():
                try:
                    panel_config.sub_path = validate_optional_transport_path_value(result.stdout.strip())
                except ValueError as exc:
                    fail(f"Recovered connection-page path is invalid: {exc}", hint_type="system")
    except (OSError, SSHError):
        pass
    if not metadata.get("public_key"):
        fail(
            "Reality public key could not be recovered",
            hint="Restore SSH access to the selected panel node and retry; no local state was written.",
            hint_type="system",
        )

    nodes: list[NodeEntry] = []
    try:
        for api_node in api_nodes:
            is_panel_host = api_node.uuid == selected_panel_node.uuid
            node_ip = panel_server_ip if is_panel_host else str(ipaddress.ip_address(api_node.address))
            node_name = validate_server_title_value(api_node.name) if api_node.name else node_ip
            if not api_node.uuid or not api_node.uuid.isprintable() or len(api_node.uuid) > 120:
                raise ValueError("Panel node UUID is missing or invalid.")
            nodes.append(
                NodeEntry(
                    ip=node_ip,
                    uuid=api_node.uuid,
                    name=node_name,
                    is_panel_host=is_panel_host,
                    ssh_user=user if is_panel_host else "root",
                    ssh_port=ssh_port if is_panel_host else 22,
                    domain=node_domains.get(api_node.uuid, ""),
                    sni=metadata["sni"],
                    reality_public_key=metadata["public_key"],
                    reality_private_key=metadata["private_key"],
                    reality_short_id=metadata["short_id"],
                    xhttp_path=metadata.get("xhttp_path", ""),
                    ws_path=metadata.get("ws_path", ""),
                )
            )
    except (TypeError, ValueError) as exc:
        fail(
            f"Panel node metadata is unsafe to recover: {exc}",
            hint="Repair node names/addresses in the panel or restore cluster.yml from backup.",
            hint_type="system",
        )

    cluster = ClusterConfig(
        panel=panel_config,
        config_profile_uuid=config_profile_uuid,
        config_profile_name=config_profile_name,
        squad_uuid=selected_squad.uuid,
        nodes=nodes,
        inbounds=inbounds,
    )

    from meridian.config import SERVER_PROFILES_FILE
    from meridian.servers import ServerEntry, ServerRegistry

    registry = ServerRegistry(SERVER_PROFILES_FILE)
    recovered_servers = [
        ServerEntry(
            host=node.ip,
            user=node.ssh_user,
            name=node.name or node.ip,
            port=node.ssh_port,
            auth_state="validated" if node.is_panel_host and panel_ssh_validated else "unknown",
        )
        for node in nodes
    ]
    try:
        for entry in recovered_servers:
            registry.assert_can_add(entry)
    except LocalStateError as exc:
        fail(
            "Recovered servers conflict with the saved server registry",
            hint=f"{exc} Resolve the saved identity conflict before retrying recovery.",
            hint_type="user",
        )

    if CLUSTER_CONFIG.exists():
        if not force:
            fail(
                "cluster.yml appeared while recovery was running",
                hint="Review the new file before retrying with --force.",
                hint_type="user",
            )
        ClusterConfig().backup()
    try:
        cluster.save()
    except (OSError, ValueError) as exc:
        fail(
            f"Could not save recovered cluster state: {exc}",
            hint="Check ~/.meridian permissions and available disk space.",
            hint_type="system",
        )
    try:
        for entry in recovered_servers:
            registry.add(entry)
    except LocalStateError as exc:
        fail(
            "Recovered cluster.yml was saved, but saved server profiles could not be completed",
            hint=f"{exc} Resolve the profile conflict, then rerun recovery with --force.",
            hint_type=exc.category,
        )
    except OSError as exc:
        fail(
            "Recovered cluster.yml was saved, but saved server profiles could not be completed",
            hint=f"{exc} Check ~/.meridian permissions and disk space, then rerun recovery with --force.",
            hint_type="system",
        )
    ok("Cluster config saved to ~/.meridian/cluster.yml")

    # Print summary
    err_console.print()
    err_console.print("  [bold]Recovered cluster[/bold]")
    err_console.print(f"    Panel:     {escape(panel_public_url)}")
    err_console.print(f"    Nodes:     {len(nodes)}")
    for node in nodes:
        role = " (panel)" if node.is_panel_host else ""
        err_console.print(f"      {escape(node.ip)}  {escape(node.name)}{role}")
    err_console.print(f"    Inbounds:  {len(inbounds)}")
    if config_profile_name:
        err_console.print(f"    Profile:   {escape(config_profile_name)}")
    if metadata.get("sni"):
        err_console.print(f"    SNI:       {escape(metadata['sni'])}")

    err_console.print()
    warn("Recovered legacy-compatible state only; V4 intent and relays require explicit setup and review")
    err_console.print()
    err_console.print("  [dim]Fleet status:   meridian fleet status[/dim]")
    err_console.print("  [dim]List nodes:     meridian node list[/dim]")
    err_console.print("  [dim]List clients:   meridian client list[/dim]")
    err_console.print()
