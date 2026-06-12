"""Xray client binary management and connection testing.

Downloads, caches, and runs an xray client binary to verify proxy
connections work end-to-end through SOCKS5.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from meridian.config import (
    CONNECT_TEST_URL,
    DEFAULT_FINGERPRINT,
    DEFAULT_SNI,
    MERIDIAN_HOME,
    XRAY_ASSET_MAP,
    XRAY_GITHUB_URL,
    XRAY_VERSION,
)
from meridian.health import poll_until_ready

if TYPE_CHECKING:
    from meridian.cluster import ClusterConfig
    from meridian.credentials import ServerCredentials


def _xray_bin_path() -> Path:
    """Return the cached xray binary path."""
    return MERIDIAN_HOME / "bin" / f"xray-{XRAY_VERSION}"


def _resolve_asset_name() -> str | None:
    """Map current platform to xray release asset filename."""
    system = platform.system()
    machine = platform.machine()
    return XRAY_ASSET_MAP.get((system, machine))


def ensure_xray_binary() -> Path | None:
    """Download xray binary if not cached. Returns path or None on failure."""
    bin_path = _xray_bin_path()
    if bin_path.exists() and os.access(bin_path, os.X_OK):
        return bin_path

    asset_name = _resolve_asset_name()
    if not asset_name:
        return None

    url = f"{XRAY_GITHUB_URL}/v{XRAY_VERSION}/{asset_name}"
    dgst_url = f"{url}.dgst"

    tmp_zip = None
    try:
        # Download digest file for SHA256 verification
        dgst_result = subprocess.run(
            ["curl", "-fsSL", "--max-time", "15", dgst_url],
            capture_output=True,
            text=True,
            timeout=20,
            stdin=subprocess.DEVNULL,
        )
        expected_sha256 = _parse_dgst(dgst_result.stdout) if dgst_result.returncode == 0 else ""

        # Download binary zip
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_zip = tmp.name

        dl_result = subprocess.run(
            ["curl", "-fsSL", "--max-time", "120", "-o", tmp_zip, url],
            capture_output=True,
            text=True,
            timeout=130,
            stdin=subprocess.DEVNULL,
        )
        if dl_result.returncode != 0:
            return None

        # Verify SHA256
        if expected_sha256:
            import hashlib

            sha256 = hashlib.sha256(Path(tmp_zip).read_bytes()).hexdigest()
            if sha256 != expected_sha256:
                Path(tmp_zip).unlink(missing_ok=True)
                return None

        # Extract xray binary from zip
        bin_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(tmp_zip) as zf:
            # Find the xray executable in the archive
            xray_name = next((n for n in zf.namelist() if n.lower() in ("xray", "xray.exe")), None)
            if not xray_name:
                return None
            with zf.open(xray_name) as src, open(bin_path, "wb") as dst:
                dst.write(src.read())

        bin_path.chmod(0o755)
        return bin_path

    except (subprocess.TimeoutExpired, FileNotFoundError, zipfile.BadZipFile, OSError):
        return None
    finally:
        if tmp_zip:
            Path(tmp_zip).unlink(missing_ok=True)


def _parse_dgst(content: str) -> str:
    """Extract SHA2-256 hash from xray .dgst file content."""
    for line in content.splitlines():
        if line.startswith("SHA2-256="):
            return line.split("=", 1)[1].strip()
    return ""


# ---------------------------------------------------------------------------
# Client config generation
# ---------------------------------------------------------------------------


def build_reality_config(
    socks_port: int,
    server_ip: str,
    uuid: str,
    sni: str,
    public_key: str,
    short_id: str,
    encryption: str = "none",
    fingerprint: str = DEFAULT_FINGERPRINT,
    server_port: int = 443,
) -> dict:
    """Build xray client config for VLESS+Reality."""
    return {
        "log": {"loglevel": "none"},
        "inbounds": [_socks_inbound(socks_port)],
        "outbounds": [
            {
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": server_ip,
                            "port": server_port,
                            "users": [
                                {
                                    "id": uuid,
                                    "encryption": encryption,
                                    "flow": "xtls-rprx-vision",
                                }
                            ],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "publicKey": public_key,
                        "fingerprint": fingerprint,
                        "serverName": sni,
                        "shortId": short_id,
                    },
                },
            }
        ],
    }


def build_xhttp_config(
    socks_port: int,
    host: str,
    uuid: str,
    xhttp_path: str,
    fingerprint: str = DEFAULT_FINGERPRINT,
    server_port: int = 443,
) -> dict:
    """Build xray client config for VLESS+XHTTP (TLS)."""
    return {
        "log": {"loglevel": "none"},
        "inbounds": [_socks_inbound(socks_port)],
        "outbounds": [
            {
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": host,
                            "port": server_port,
                            "users": [{"id": uuid, "encryption": "none"}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "xhttp",
                    "security": "tls",
                    "tlsSettings": {
                        "serverName": host,
                        "fingerprint": fingerprint,
                    },
                    "xhttpSettings": {"path": f"/{xhttp_path}"},
                },
            }
        ],
    }


def build_wss_config(
    socks_port: int,
    domain: str,
    uuid: str,
    ws_path: str,
) -> dict:
    """Build xray client config for VLESS+WSS (TLS)."""
    return {
        "log": {"loglevel": "none"},
        "inbounds": [_socks_inbound(socks_port)],
        "outbounds": [
            {
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": domain,
                            "port": 443,
                            "users": [{"id": uuid, "encryption": "none"}],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "ws",
                    "security": "tls",
                    "tlsSettings": {"serverName": domain},
                    "wsSettings": {
                        "path": f"/{ws_path}",
                        "headers": {"Host": domain},
                    },
                },
            }
        ],
    }


def _socks_inbound(port: int) -> dict:
    return {
        "protocol": "socks",
        "listen": "127.0.0.1",
        "port": port,
        "settings": {"auth": "noauth"},
    }


def _find_free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Connection testing
# ---------------------------------------------------------------------------


def test_connection(
    xray_bin: Path,
    config: dict,
    server_ip: str,
    socks_port: int,
    label: str,
    expect_ip_match: bool = True,
) -> tuple[bool, str]:
    """Start xray client, curl through SOCKS5, verify connectivity.

    Args:
        xray_bin: Path to xray binary.
        config: Xray client config dict.
        server_ip: Expected exit IP (for Reality).
        socks_port: SOCKS5 port to use.
        label: Protocol label for display.
        expect_ip_match: If True, verify exit IP matches server_ip.

    Returns:
        (success, detail_message) tuple.
    """
    config_file = None
    proc = None
    try:
        # Write config to temp file
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix="meridian-xray-",
            delete=False,
        ) as f:
            json.dump(config, f)
            config_file = f.name

        # Start xray client
        proc = subprocess.Popen(
            [str(xray_bin), "run", "-c", config_file],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
        )

        # Wait for SOCKS5 to be ready
        _wait_for_port(socks_port, timeout=5)

        # Test connectivity
        start = time.monotonic()
        result = subprocess.run(
            [
                "curl",
                "-sS",
                "--socks5-hostname",
                f"127.0.0.1:{socks_port}",
                "--connect-timeout",
                "10",
                "--max-time",
                "15",
                CONNECT_TEST_URL,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            stdin=subprocess.DEVNULL,
        )
        elapsed = time.monotonic() - start

        exit_ip = result.stdout.strip()
        if result.returncode != 0 or not exit_ip:
            stderr_hint = result.stderr.strip()[:100] if result.stderr else ""
            return False, f"no response{f' ({stderr_hint})' if stderr_hint else ''}"

        if expect_ip_match and exit_ip != server_ip:
            return False, f"exit IP {exit_ip} does not match server {server_ip}"

        return True, f"exit IP {exit_ip} ({elapsed:.1f}s)"

    except subprocess.TimeoutExpired:
        return False, "timeout"
    except FileNotFoundError:
        return False, "curl not found"
    except OSError as e:
        return False, str(e)
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        if config_file:
            Path(config_file).unlink(missing_ok=True)


def _wait_for_port(port: int, timeout: float = 5) -> None:
    """Wait until a TCP port is accepting connections on localhost."""

    def _check() -> bool:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True

    poll_until_ready(
        _check,
        timeout=timeout,
        interval=0.2,
        description=f"localhost:{port}",
    )


def build_test_configs(creds: ServerCredentials) -> list[tuple[str, dict, bool]]:
    """Build test configs for all active protocols.

    Returns list of (label, config_dict, expect_ip_match) tuples.
    """
    ip = creds.server.ip or ""
    sni = creds.server.sni or DEFAULT_SNI
    domain = creds.server.domain or ""
    warp = creds.server.warp
    public_key = creds.reality.public_key or ""
    short_id = creds.reality.short_id or ""
    reality_uuid = creds.reality.uuid or ""
    encryption = creds.reality.encryption_key or "none"
    xhttp_path = creds.xhttp.xhttp_path or ""
    wss_uuid = creds.wss.uuid or ""
    ws_path = creds.wss.ws_path or ""

    configs: list[tuple[str, dict, bool]] = []

    if reality_uuid and public_key:
        port = _find_free_port()
        configs.append(
            (
                "Reality (TCP)",
                build_reality_config(port, ip, reality_uuid, sni, public_key, short_id, encryption),
                not warp,  # WARP: exit IP is Cloudflare, not server
            )
        )

    if xhttp_path and reality_uuid:
        host = domain or ip
        port = _find_free_port()
        configs.append(
            (
                "XHTTP",
                build_xhttp_config(port, host, reality_uuid, xhttp_path),
                not domain and not warp,  # IP mode without WARP: expect match
            )
        )

    if domain and wss_uuid and ws_path:
        port = _find_free_port()
        configs.append(
            (
                "WSS (CDN)",
                build_wss_config(port, domain, wss_uuid, ws_path),
                False,  # CDN exit IP differs from server
            )
        )

    # Relay configs — test each relay's Reality and XHTTP paths
    for relay in creds.relays:
        relay_label = relay.name or relay.ip
        relay_sni = relay.sni or sni

        if reality_uuid and public_key:
            port = _find_free_port()
            configs.append(
                (
                    f"Reality via {relay_label}",
                    build_reality_config(
                        port,
                        relay.ip,
                        reality_uuid,
                        relay_sni,
                        public_key,
                        short_id,
                        encryption,
                        server_port=relay.port,
                    ),
                    not warp,
                )
            )

        if xhttp_path and reality_uuid:
            xhttp_host = domain or ip
            port = _find_free_port()
            # XHTTP via relay: connect to relay_ip:relay_port, TLS sni=exit
            cfg = build_xhttp_config(port, xhttp_host, reality_uuid, xhttp_path, server_port=relay.port)
            # Override address to relay IP (TLS serverName stays as exit)
            cfg["outbounds"][0]["settings"]["vnext"][0]["address"] = relay.ip
            configs.append(
                (
                    f"XHTTP via {relay_label}",
                    cfg,
                    not domain and not warp,
                )
            )

    return configs


def build_test_configs_from_cluster(
    cluster: ClusterConfig,
    node_ip: str,
    *,
    uuid: str = "",
) -> list[tuple[str, dict, bool]]:
    """Build test configs from ClusterConfig data (v4).

    Uses node metadata (Reality keys, paths) from cluster.yml instead of
    the legacy ServerCredentials/proxy.yml format.

    Args:
        cluster: Loaded ClusterConfig.
        node_ip: IP of the node to test.
        uuid: Client UUID for building test connections. If empty, a dummy
              UUID is used (connection will fail auth but tests reachability).

    Returns list of (label, config_dict, expect_ip_match) tuples.
    """
    node = cluster.find_node(node_ip)
    if node is None:
        return []

    ip = node.ip
    sni = node.sni or DEFAULT_SNI
    domain = node.domain or ""
    public_key = node.reality_public_key or ""
    short_id = node.reality_short_id or ""
    xhttp_path = node.xhttp_path or ""
    ws_path = node.ws_path or ""
    warp = getattr(node, "warp", False)
    test_uuid = uuid or "00000000-0000-0000-0000-000000000000"

    configs: list[tuple[str, dict, bool]] = []

    # Reality (always present)
    if public_key:
        port = _find_free_port()
        configs.append(
            (
                "Reality (TCP)",
                build_reality_config(port, ip, test_uuid, sni, public_key, short_id),
                not warp,
            )
        )

    # XHTTP
    if xhttp_path:
        host = domain or ip
        port = _find_free_port()
        configs.append(
            (
                "XHTTP",
                build_xhttp_config(port, host, test_uuid, xhttp_path),
                not domain and not warp,
            )
        )

    # WSS (domain mode only)
    if domain and ws_path:
        port = _find_free_port()
        configs.append(
            (
                "WSS (CDN)",
                build_wss_config(port, domain, test_uuid, ws_path),
                False,
            )
        )

    # Relay configs
    for relay in cluster.relays:
        if relay.exit_node_ip and relay.exit_node_ip != ip:
            continue  # This relay forwards to a different node
        relay_label = relay.name or relay.ip
        relay_sni = relay.sni or sni

        if public_key:
            port = _find_free_port()
            configs.append(
                (
                    f"Reality via {relay_label}",
                    build_reality_config(
                        port,
                        relay.ip,
                        test_uuid,
                        relay_sni,
                        public_key,
                        short_id,
                        server_port=relay.port,
                    ),
                    not warp,
                )
            )

        if xhttp_path:
            xhttp_host = domain or ip
            port = _find_free_port()
            cfg = build_xhttp_config(port, xhttp_host, test_uuid, xhttp_path, server_port=relay.port)
            cfg["outbounds"][0]["settings"]["vnext"][0]["address"] = relay.ip
            configs.append(
                (
                    f"XHTTP via {relay_label}",
                    cfg,
                    not domain and not warp,
                )
            )

    return configs
