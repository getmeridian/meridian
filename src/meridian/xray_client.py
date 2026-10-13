"""Xray client binary management and connection testing.

Downloads, caches, and runs an xray client binary to verify proxy
connections work end-to-end through SOCKS5.
"""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import os
import platform
import socket
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from meridian.config import (
    CONNECT_TEST_URLS_IPV4,
    CONNECT_TEST_URLS_IPV6,
    MERIDIAN_HOME,
    XRAY_ASSET_MAP,
    XRAY_GITHUB_URL,
    XRAY_VERSION,
)
from meridian.core.errors import MeridianError
from meridian.health import ReadinessTimeout, poll_until_ready

_XRAY_RUNTIME_ASSETS = ("geoip.dat", "geosite.dat")
_CONNECTION_ATTEMPTS = 3
_CONNECTION_RETRY_DELAY = 1.0
_PROXY_TAG_PREFIX = "MERIDIAN_PROXY"
_SAFE_LOCAL_INBOUND_PROTOCOLS = {"http", "socks"}
XRAY_BOOTSTRAP_TIMEOUT = 120.0
_TIMING_MARKER = "\n@@meridian-ttfb@@"


class XrayStartupError(MeridianError):
    """The local Xray process exited or never opened its client listener."""

    def __init__(self, message: str) -> None:
        super().__init__(message, category="system", retryable=True)


class XrayConfigError(ValueError):
    """The delivered canonical config was rejected by the local Xray binary."""


# Traffic flowed, but left through an unexpected IP (NAT, multi-IP hosts, misrouting).
EGRESS_MISMATCH_PREFIX = "exit IP"


@dataclass(frozen=True)
class ConnectionAttempt:
    """Outcome of pushing traffic through one local Xray client.

    ``success`` is None when the external IP observers, not the proxy, failed.
    ``first_byte_ms`` is the observer response's time to first byte through the proxy.
    """

    success: bool | None
    detail: str
    first_byte_ms: int | None = None


def _xray_bin_path() -> Path:
    """Return the cached xray binary path."""
    return MERIDIAN_HOME / "bin" / f"xray-{XRAY_VERSION}"


def _xray_asset_version_path(bin_path: Path) -> Path:
    return bin_path.parent / ".xray-assets-version"


def _xray_cache_is_complete(bin_path: Path) -> bool:
    try:
        if not bin_path.is_file() or not os.access(bin_path, os.X_OK) or bin_path.stat().st_size == 0:
            return False
        if _xray_asset_version_path(bin_path).read_text(encoding="utf-8").strip() != XRAY_VERSION:
            return False
        return all(
            (bin_path.parent / name).is_file() and (bin_path.parent / name).stat().st_size > 0
            for name in _XRAY_RUNTIME_ASSETS
        )
    except OSError:
        return False


def _resolve_asset_name() -> str | None:
    """Map current platform to xray release asset filename."""
    system = platform.system()
    machine = platform.machine()
    return XRAY_ASSET_MAP.get((system, machine))


def ensure_xray_binary(*, timeout: float = XRAY_BOOTSTRAP_TIMEOUT) -> Path | None:
    """Download Xray within one shared deadline, or return a complete cache."""
    bin_path = _xray_bin_path()
    if _xray_cache_is_complete(bin_path):
        return bin_path

    asset_name = _resolve_asset_name()
    if not asset_name:
        return None

    url = f"{XRAY_GITHUB_URL}/v{XRAY_VERSION}/{asset_name}"
    dgst_url = f"{url}.dgst"

    tmp_zip = None
    deadline = time.monotonic() + max(1.0, float(timeout))
    try:
        # Download digest file for SHA256 verification
        digest_timeout = _remaining_timeout(deadline)
        dgst_result = subprocess.run(
            ["curl", "-fsSL", "--max-time", f"{digest_timeout:g}", dgst_url],
            capture_output=True,
            text=True,
            timeout=digest_timeout,
            stdin=subprocess.DEVNULL,
        )
        if dgst_result.returncode != 0:
            return None
        expected_sha256 = _parse_dgst(dgst_result.stdout)
        if not expected_sha256:
            return None

        # Download binary zip
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_zip = tmp.name

        archive_timeout = _remaining_timeout(deadline)
        dl_result = subprocess.run(
            ["curl", "-fsSL", "--max-time", f"{archive_timeout:g}", "-o", tmp_zip, url],
            capture_output=True,
            text=True,
            timeout=archive_timeout,
            stdin=subprocess.DEVNULL,
        )
        if dl_result.returncode != 0:
            return None

        # Refuse to execute a release whose published checksum cannot be verified.
        sha256 = hashlib.sha256(Path(tmp_zip).read_bytes()).hexdigest()
        if sha256 != expected_sha256:
            Path(tmp_zip).unlink(missing_ok=True)
            return None

        # Xray's geoip:/geosite: routing rules load data files beside the binary.
        # Publish the complete set only after every archive member is staged.
        bin_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".xray-stage-", dir=bin_path.parent) as stage_dir:
            stage = Path(stage_dir)
            staged_bin = stage / bin_path.name
            with zipfile.ZipFile(tmp_zip) as zf:
                archive_files = {Path(name).name.lower(): name for name in zf.namelist() if Path(name).name}
                xray_name = archive_files.get("xray") or archive_files.get("xray.exe")
                if not xray_name:
                    return None
                asset_names = {name: archive_files.get(name) for name in _XRAY_RUNTIME_ASSETS}
                if any(name is None for name in asset_names.values()):
                    return None
                with zf.open(xray_name) as src, staged_bin.open("wb") as dst:
                    dst.write(src.read())
                for runtime_asset, archive_name in asset_names.items():
                    if archive_name is None:
                        return None
                    with zf.open(archive_name) as src, (stage / runtime_asset).open("wb") as dst:
                        dst.write(src.read())
            staged_paths = [staged_bin, *(stage / name for name in _XRAY_RUNTIME_ASSETS)]
            if any(path.stat().st_size == 0 for path in staged_paths):
                return None
            staged_bin.chmod(0o755)
            staged_marker = stage / ".xray-assets-version"
            staged_marker.write_text(XRAY_VERSION + "\n", encoding="utf-8")

            marker_path = _xray_asset_version_path(bin_path)
            marker_path.unlink(missing_ok=True)
            for runtime_asset in _XRAY_RUNTIME_ASSETS:
                os.replace(stage / runtime_asset, bin_path.parent / runtime_asset)
            os.replace(staged_bin, bin_path)
            os.replace(staged_marker, marker_path)
        return bin_path

    except (subprocess.TimeoutExpired, FileNotFoundError, zipfile.BadZipFile, OSError):
        return None
    finally:
        if tmp_zip:
            Path(tmp_zip).unlink(missing_ok=True)


def _remaining_timeout(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired("Xray download", 0)
    return remaining


def _parse_dgst(content: str) -> str:
    """Extract SHA2-256 hash from xray .dgst file content."""
    for line in content.splitlines():
        if line.startswith("SHA2-256="):
            digest = line.split("=", 1)[1].strip().lower()
            if len(digest) == 64 and all(character in "0123456789abcdef" for character in digest):
                return digest
            return ""
    return ""


def _find_free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Canonical subscription handling
# ---------------------------------------------------------------------------


def parse_xray_subscription(content: str) -> dict[str, Any]:
    """Decode a canonical Remnawave Xray subscription and return its primary config.

    Remnawave answers with one config per virtual Host in view order: the primary
    "Automatic" config first, then the optional fingerprint-copy config. Every
    entry must be valid; only the primary is returned.
    """
    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Canonical Xray subscription is not JSON: {exc}") from exc
    if isinstance(parsed, list):
        if not parsed:
            raise ValueError("Canonical Xray subscription contains no configs")
        configs = [_subscription_config(item) for item in parsed]
        return configs[0]
    return _subscription_config(parsed)


def _subscription_config(parsed: Any) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        raise ValueError("Canonical Xray subscription must be an object")
    if not isinstance(parsed.get("outbounds"), list):
        raise ValueError("Canonical Xray subscription has no outbounds")
    if not proxy_outbounds(parsed):
        raise ValueError("Canonical Xray subscription has no Meridian proxy outbounds")
    routing = parsed.get("routing")
    if routing is not None and not isinstance(routing, dict):
        raise ValueError("Canonical Xray routing must be an object")
    _validate_local_inbounds(parsed)
    return parsed


def proxy_outbounds(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return proxy outbounds owned by Meridian's tag prefix."""
    outbounds = config.get("outbounds")
    if not isinstance(outbounds, list):
        return []
    return [
        outbound
        for outbound in outbounds
        if isinstance(outbound, dict)
        and isinstance(outbound.get("tag"), str)
        and outbound["tag"].startswith(_PROXY_TAG_PREFIX)
    ]


def is_supported_proxy_outbound(outbound: dict[str, Any]) -> bool:
    """Return whether an outbound uses a supported canonical wire shape."""
    if outbound.get("protocol") == "vless":
        return True
    settings = outbound.get("settings")
    return outbound.get("protocol") == "hysteria" and isinstance(settings, dict) and settings.get("version") == 2


def outbound_address(outbound: dict[str, Any]) -> str:
    """Return a canonical VLESS or Hysteria v2 endpoint address."""
    settings = outbound.get("settings")
    if not isinstance(settings, dict):
        return ""
    if outbound.get("protocol") == "hysteria" and settings.get("version") == 2:
        address = settings.get("address")
        return address.strip() if isinstance(address, str) else ""
    if outbound.get("protocol") != "vless":
        return ""
    destinations = settings.get("vnext")
    if not isinstance(destinations, list) or not destinations:
        return ""
    destination = destinations[0]
    if not isinstance(destination, dict):
        return ""
    address = destination.get("address")
    return address.strip() if isinstance(address, str) else ""


def outbound_tcp_endpoint(outbound: dict[str, Any]) -> tuple[str, int] | None:
    """Return a VLESS outbound's TCP address and port; None for UDP or malformed shapes."""
    if outbound.get("protocol") != "vless":
        return None
    address = outbound_address(outbound)
    destinations = outbound.get("settings", {}).get("vnext")
    port = destinations[0].get("port") if isinstance(destinations, list) and destinations else None
    if not address or not isinstance(port, int) or not 1 <= port <= 65535:
        return None
    return address, port


def outbound_label(outbound: dict[str, Any]) -> str:
    """Derive a credential-free display label for a canonical outbound, named like its entry.

    ``Reality · Chrome 198.51.100.10``, ``XHTTP · Firefox …``, ``Hysteria2 · UDP …``.
    """
    from meridian.compiler.names import host_remark

    settings = outbound.get("settings")
    stream = outbound.get("streamSettings")
    stream = stream if isinstance(stream, dict) else {}
    if outbound.get("protocol") == "vless":
        key = _vless_method(stream)
        security = stream.get("realitySettings") or stream.get("tlsSettings")
        fingerprint = security.get("fingerprint") if isinstance(security, dict) else ""
        protocol = host_remark(key, fingerprint=_safe_label_component(fingerprint)) if key else "VLESS"
    elif outbound.get("protocol") == "hysteria" and isinstance(settings, dict) and settings.get("version") == 2:
        protocol = host_remark("hysteria2")
    else:
        protocol = "Xray"
    address = _safe_label_component(outbound_address(outbound))
    if address:
        return f"{protocol} {address}"
    tag = _safe_label_component(outbound.get("tag"))
    return f"{protocol} {tag or 'endpoint'}"


def _vless_method(stream: dict[str, Any]) -> str:
    if stream.get("security") == "reality":
        return "reality"
    return {"xhttp": "xhttp", "ws": "wss"}.get(str(stream.get("network") or ""), "")


def _safe_label_component(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(character for character in value.strip() if character.isprintable() and character not in "[]")[:160]


def endpoint_addresses(config: dict[str, Any]) -> set[str]:
    """Return concrete endpoint addresses from managed proxy outbounds."""
    return {address for outbound in proxy_outbounds(config) if (address := outbound_address(outbound))}


def select_outbound(config: dict[str, Any], address: str) -> str:
    """Return the managed outbound tag for one delivered endpoint address."""
    for outbound in proxy_outbounds(config):
        if outbound_address(outbound) == address:
            tag = outbound.get("tag")
            if isinstance(tag, str) and tag:
                return tag
    raise ValueError(f"Canonical subscription has no endpoint for {address}")


def route_only(config: dict[str, Any], outbound_tag: str) -> dict[str, Any]:
    """Pin a canonical config to one managed outbound without rebuilding it."""
    known_tags = {str(outbound.get("tag", "")) for outbound in proxy_outbounds(config)}
    if outbound_tag not in known_tags:
        raise ValueError(f"Canonical subscription has no outbound {outbound_tag!r}")
    selected = copy.deepcopy(config)
    routing = selected.setdefault("routing", {})
    if not isinstance(routing, dict):
        raise ValueError("Canonical Xray routing must be an object")
    routing["rules"] = [
        {
            "type": "field",
            "ip": ["geoip:private"],
            "outboundTag": "BLOCK",
        },
        {
            "type": "field",
            "network": "tcp,udp",
            "outboundTag": outbound_tag,
        },
    ]
    routing.pop("balancers", None)
    selected.pop("burstObservatory", None)
    selected.pop("observatory", None)
    return selected


def use_free_local_ports(config: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Assign collision-free ephemeral ports to canonical local inbounds."""
    selected = copy.deepcopy(config)
    selected["log"] = {"loglevel": "none"}
    selected.pop("api", None)
    selected.pop("stats", None)
    inbounds = _validate_local_inbounds(selected)
    socks_port = 0
    used_ports: set[int] = set()
    for inbound in inbounds:
        if not isinstance(inbound, dict):
            continue
        port = _find_free_port()
        while port in used_ports:
            port = _find_free_port()
        used_ports.add(port)
        inbound["listen"] = "127.0.0.1"
        inbound["port"] = port
        if inbound.get("protocol") == "socks":
            socks_port = port
    if not socks_port:
        raise ValueError("Canonical Xray subscription has no SOCKS inbound")
    return selected, socks_port


def _validate_local_inbounds(config: dict[str, Any]) -> list[dict[str, Any]]:
    inbounds = config.get("inbounds")
    if not isinstance(inbounds, list) or not inbounds:
        raise ValueError("Canonical Xray subscription has no local inbounds")
    validated: list[dict[str, Any]] = []
    for inbound in inbounds:
        if not isinstance(inbound, dict):
            raise ValueError("Canonical Xray subscription contains an invalid local inbound")
        protocol = inbound.get("protocol")
        if protocol not in _SAFE_LOCAL_INBOUND_PROTOCOLS:
            raise ValueError(f"Canonical Xray subscription contains unsafe local inbound protocol {protocol!r}")
        validated.append(inbound)
    if not any(inbound.get("protocol") == "socks" for inbound in validated):
        raise ValueError("Canonical Xray subscription has no SOCKS inbound")
    return validated


# ---------------------------------------------------------------------------
# Connection testing
# ---------------------------------------------------------------------------


def _validate_xray_config(xray_bin: Path, config_file: str, *, timeout: float) -> None:
    """Ask Xray to validate the exact config without opening listeners."""
    missing = [name for name in _XRAY_RUNTIME_ASSETS if not (xray_bin.parent / name).is_file()]
    if not xray_bin.is_file() or not os.access(xray_bin, os.X_OK):
        raise XrayStartupError(f"local Xray executable is unavailable: {xray_bin}")
    if missing:
        raise XrayStartupError("local Xray runtime is incomplete: missing " + ", ".join(missing))

    operation_timeout = max(1.0, float(timeout))
    try:
        result = subprocess.run(
            [str(xray_bin), "run", "-test", "-c", config_file],
            capture_output=True,
            text=True,
            timeout=operation_timeout,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise XrayStartupError("local Xray config validation timed out") from exc
    except FileNotFoundError as exc:
        raise XrayStartupError(f"local Xray executable was not found: {exc.filename or exc}") from exc
    except OSError as exc:
        raise XrayStartupError(f"local Xray config validation could not run: {exc}") from exc

    if result.returncode != 0:
        raise XrayConfigError(f"Xray rejected the canonical config (exit {result.returncode})")


def observer_urls(server_ip: str) -> tuple[str, ...]:
    """Return exit-IP observers that answer only over the server's address family."""
    try:
        if ipaddress.ip_address(server_ip).version == 6:
            return CONNECT_TEST_URLS_IPV6
    except ValueError:
        pass
    return CONNECT_TEST_URLS_IPV4


def test_connection(
    xray_bin: Path,
    config: dict[str, Any],
    server_ip: str,
    socks_port: int,
    label: str,
    expect_ip_match: bool = True,
    *,
    timeout: float = 15,
) -> ConnectionAttempt:
    """Start xray client, curl through SOCKS5, verify connectivity.

    Args:
        xray_bin: Path to xray binary.
        config: Xray client config dict.
        server_ip: Expected exit IP (for Reality).
        socks_port: SOCKS5 port to use.
        label: Protocol label for display.
        expect_ip_match: If True, verify exit IP matches server_ip.

    Returns:
        The attempt's success, detail message, and time to first byte.

    Raises:
        XrayConfigError: If Xray rejects the delivered canonical config.
        XrayStartupError: If Xray or a required local test command cannot run.
    """
    config_file: str | None = None
    proc: subprocess.Popen[str] | None = None
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

        operation_timeout = max(1.0, float(timeout))
        _validate_xray_config(xray_bin, config_file, timeout=operation_timeout)

        # Start xray client
        proc = subprocess.Popen(
            [str(xray_bin), "run", "-c", config_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
        )

        # Wait for SOCKS5 to be ready
        def _socks_ready() -> bool:
            with socket.create_connection(("127.0.0.1", socks_port), timeout=0.5):
                return True

        poll_until_ready(
            _socks_ready,
            timeout=min(5.0, operation_timeout),
            interval=0.2,
            description=f"localhost:{socks_port}",
            transient=(OSError,),
        )

        # Test connectivity. A node can report connected just before its active
        # profile accepts the first Reality handshake, so require a sustained
        # failure without restarting the local Xray process.
        start = time.monotonic()
        last_detail = "no response"
        attempted_urls: list[str] = []
        observers = observer_urls(server_ip)
        for attempt in range(1, _CONNECTION_ATTEMPTS + 1):
            observer_url = observers[(attempt - 1) % len(observers)]
            attempted_urls.append(observer_url)
            try:
                observed_ip, last_detail, first_byte_ms = _query_ip_observer(
                    observer_url,
                    socks_port=socks_port,
                    timeout=operation_timeout,
                )
                if observed_ip is not None:
                    if expect_ip_match:
                        try:
                            expected_ip = ipaddress.ip_address(server_ip)
                        except ValueError:
                            return ConnectionAttempt(False, f"expected server IP {server_ip!r} is invalid")
                        if observed_ip != expected_ip:
                            return ConnectionAttempt(
                                False,
                                f"{EGRESS_MISMATCH_PREFIX} {observed_ip} does not match server {expected_ip}",
                                first_byte_ms,
                            )
                    elapsed = time.monotonic() - start
                    first_byte = f"first byte {first_byte_ms} ms, " if first_byte_ms is not None else ""
                    return ConnectionAttempt(
                        True,
                        f"exit IP {observed_ip} ({first_byte}{elapsed:.1f}s, attempt {attempt})",
                        first_byte_ms,
                    )
            except subprocess.TimeoutExpired:
                last_detail = "timeout"

            returncode = proc.poll()
            if returncode is not None:
                raise XrayStartupError(f"xray exited during connection test (exit {returncode})")
            if attempt < _CONNECTION_ATTEMPTS:
                time.sleep(_CONNECTION_RETRY_DELAY)

        # Separate observer failure from proxy failure. If at least one of the
        # same endpoints works without SOCKS, the proxy path supplied negative
        # evidence. If none work directly, the external oracle is unavailable
        # and the caller must report an inconclusive test rather than a defect.
        for observer_url in dict.fromkeys(attempted_urls):
            try:
                control_ip, _, _ = _query_ip_observer(observer_url, socks_port=None, timeout=operation_timeout)
            except subprocess.TimeoutExpired:
                continue
            if control_ip is not None:
                return ConnectionAttempt(
                    False, f"{last_detail} after {_CONNECTION_ATTEMPTS} attempts; observer works directly"
                )
        return ConnectionAttempt(None, f"{last_detail} after {_CONNECTION_ATTEMPTS} attempts; IP observers unavailable")

    except ReadinessTimeout as exc:
        returncode = proc.poll() if proc is not None else None
        if returncode is not None:
            raise XrayStartupError(f"xray failed to open its local listener (exit {returncode})") from exc
        raise XrayStartupError(f"xray failed to start: {exc}") from exc
    except FileNotFoundError as exc:
        raise XrayStartupError(f"connection test command not found: {exc.filename or exc}") from exc
    except OSError as exc:
        raise XrayStartupError(f"connection test could not run: {exc}") from exc
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        if config_file:
            Path(config_file).unlink(missing_ok=True)


def _query_ip_observer(
    url: str,
    *,
    socks_port: int | None,
    timeout: float,
) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address | None, str, int | None]:
    """Return the observed IP (or None with a reason) and the time to first byte in ms."""
    args = [
        "curl",
        "-sS",
        "--fail",
        "--write-out",
        _TIMING_MARKER + "%{time_starttransfer}",
        "--connect-timeout",
        f"{min(10.0, timeout):g}",
        "--max-time",
        f"{timeout:g}",
    ]
    if socks_port is not None:
        args.extend(("--socks5-hostname", f"127.0.0.1:{socks_port}"))
    args.append(url)
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout + 5,
        stdin=subprocess.DEVNULL,
    )
    body, marker, timing = result.stdout.rpartition(_TIMING_MARKER)
    if not marker:
        body, timing = result.stdout, ""
    try:
        first_byte_ms: int | None = round(float(timing) * 1000)
    except ValueError:
        first_byte_ms = None
    if result.returncode != 0:
        stderr_hint = result.stderr.strip()[:100] if result.stderr else ""
        return None, f"curl failed{f' ({stderr_hint})' if stderr_hint else ''}", None
    try:
        return ipaddress.ip_address(body.strip()), "", first_byte_ms
    except ValueError:
        return None, "invalid IP response", None
