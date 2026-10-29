"""Helpers for validating and executing the subscriptions a fleet delivers."""

from __future__ import annotations

import base64
import binascii
import copy
import json
import shutil
import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import yaml

from meridian.control_access import ServiceAccessPanel
from meridian.remnawave import XRAY_JSON_CLIENT_TYPE, SubscriptionDocument
from meridian.xray_client import (
    outbound_address,
    parse_xray_subscription,
    proxy_outbounds,
    route_only,
    use_free_local_ports,
)

__all__ = [
    "ENGINE_NAMES",
    "MIHOMO_CLIENT_TYPE",
    "SINGBOX_CLIENT_TYPE",
    "base64_endpoint_addresses",
    "base64_vless_user_ids",
    "check_with_engine",
    "endpoint_addresses",
    "engine_check_command",
    "fetch_canonical_document",
    "fetch_canonical_xray",
    "mihomo_endpoint_addresses",
    "mihomo_vless_user_ids",
    "missing_addresses",
    "outbound_address",
    "parse_base64_subscription",
    "parse_mihomo_subscription",
    "parse_singbox_subscription",
    "parse_xray_subscription",
    "proxy_outbounds",
    "replace_user_ids",
    "route_only",
    "select_outbound",
    "singbox_endpoint_addresses",
    "singbox_vless_user_ids",
    "use_free_local_ports",
    "xray_vless_user_ids",
]

# The /sub/{shortId}/{clientType} segments for the engine-checked formats.
SINGBOX_CLIENT_TYPE = "singbox"
MIHOMO_CLIENT_TYPE = "mihomo"
ENGINE_NAMES = {SINGBOX_CLIENT_TYPE: "sing-box", MIHOMO_CLIENT_TYPE: "Mihomo"}
_SINGBOX_GROUP_TYPES = frozenset({"selector", "urltest"})


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


def fetch_canonical_document(
    panel: ServiceAccessPanel,
    username: str,
    client_type: str,
) -> SubscriptionDocument:
    """Fetch one client format exactly as the control service renders it."""
    user = panel.get_user(username)
    if user is None or not user.short_uuid:
        raise ValueError(f"User {username!r} has no subscription")
    return panel.fetch_subscription(user.short_uuid, client_type=client_type)


def fetch_canonical_xray(
    panel: ServiceAccessPanel,
    username: str,
) -> SubscriptionDocument:
    """Fetch the Xray JSON document the control service renders."""
    return fetch_canonical_document(panel, username, XRAY_JSON_CLIENT_TYPE)


def parse_base64_subscription(content: str) -> list[str]:
    """Decode a canonical Base64 subscription into validated proxy URLs."""
    encoded = "".join(content.split())
    if not encoded:
        raise ValueError("Canonical Base64 subscription is empty")
    encoded += "=" * (-len(encoded) % 4)
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError("Canonical Base64 subscription is not valid Base64 UTF-8") from exc
    urls = [line.strip() for line in decoded.splitlines() if line.strip()]
    if not urls:
        raise ValueError("Canonical Base64 subscription contains no proxy URLs")
    for url in urls:
        parsed = urlsplit(url)
        if parsed.scheme not in {"vless", "hysteria2"}:
            raise ValueError(f"Canonical Base64 subscription contains unsupported URL scheme {parsed.scheme!r}")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Canonical Base64 subscription contains an invalid endpoint port") from exc
        if not parsed.hostname or port is None or not 1 <= port <= 65535:
            raise ValueError("Canonical Base64 subscription contains an incomplete endpoint")
        if parsed.scheme == "vless":
            try:
                UUID(parsed.username or "")
            except ValueError as exc:
                raise ValueError("Canonical Base64 subscription contains an invalid VLESS UUID") from exc
    return urls


def base64_endpoint_addresses(urls: Iterable[str]) -> set[str]:
    """Return endpoint addresses from validated Base64 subscription URLs."""
    return {address for url in urls if (address := urlsplit(url).hostname)}


def base64_vless_user_ids(urls: Iterable[str]) -> set[str]:
    """Return normalized VLESS UUIDs from validated Base64 URLs."""
    return {str(UUID(parsed.username or "")) for url in urls if (parsed := urlsplit(url)).scheme == "vless"}


def parse_mihomo_subscription(content: str) -> dict[str, Any]:
    """Parse a canonical Mihomo document and validate its fallback graph."""
    try:
        parsed = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError(f"Canonical Mihomo subscription is not YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Canonical Mihomo subscription must be an object")
    proxies = parsed.get("proxies")
    if not isinstance(proxies, list) or not proxies:
        raise ValueError("Canonical Mihomo subscription has no proxies")

    proxy_names: set[str] = set()
    for proxy in proxies:
        if not isinstance(proxy, dict):
            raise ValueError("Canonical Mihomo subscription contains a malformed proxy")
        name = proxy.get("name")
        proxy_type = proxy.get("type")
        server = proxy.get("server")
        try:
            port = int(proxy.get("port"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Canonical Mihomo subscription contains an invalid proxy port") from exc
        if not isinstance(name, str) or not name or name in proxy_names:
            raise ValueError("Canonical Mihomo subscription proxy names must be nonempty and unique")
        if not isinstance(proxy_type, str) or not proxy_type or not isinstance(server, str) or not server:
            raise ValueError("Canonical Mihomo subscription contains an incomplete proxy")
        if not 1 <= port <= 65535:
            raise ValueError("Canonical Mihomo subscription contains an invalid proxy port")
        if proxy_type == "vless":
            try:
                UUID(str(proxy.get("uuid", "")))
            except ValueError as exc:
                raise ValueError("Canonical Mihomo subscription contains an invalid VLESS UUID") from exc
        proxy_names.add(name)

    groups = parsed.get("proxy-groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError("Canonical Mihomo subscription has no proxy groups")
    fallback_names: set[str] = set()
    for group in groups:
        if not isinstance(group, dict) or group.get("type") != "fallback":
            continue
        name = group.get("name")
        members = group.get("proxies")
        if not isinstance(name, str) or not name or not isinstance(members, list) or not members:
            continue
        if not all(isinstance(member, str) and member in proxy_names for member in members):
            raise ValueError("Canonical Mihomo fallback references an unknown proxy")
        fallback_names.add(name)
    if not fallback_names:
        raise ValueError("Canonical Mihomo subscription has no populated fallback group")

    groups_by_name = {group.get("name"): group for group in groups if isinstance(group, dict)}

    def reaches_fallback(target: str) -> bool:
        # A select group starts on its first member, so one led by a fallback group routes through it.
        group = groups_by_name.get(target)
        members = group.get("proxies") if isinstance(group, dict) and group.get("type") == "select" else None
        leader = members[0] if isinstance(members, list) and members else None
        return target in fallback_names or leader in fallback_names

    rules = parsed.get("rules")
    if not isinstance(rules, list) or not any(
        isinstance(rule, str) and rule.startswith("MATCH,") and reaches_fallback(rule.removeprefix("MATCH,"))
        for rule in rules
    ):
        raise ValueError("Canonical Mihomo subscription does not route through its fallback group")
    return parsed


def mihomo_endpoint_addresses(config: dict[str, Any]) -> set[str]:
    """Return endpoint addresses from a validated Mihomo document."""
    proxies = config.get("proxies", [])
    return {
        str(proxy["server"])
        for proxy in proxies
        if isinstance(proxy, dict) and isinstance(proxy.get("server"), str) and proxy["server"]
    }


def mihomo_vless_user_ids(config: dict[str, Any]) -> set[str]:
    """Return normalized VLESS UUIDs from a validated Mihomo document."""
    return {
        str(UUID(str(proxy["uuid"])))
        for proxy in config.get("proxies", [])
        if isinstance(proxy, dict) and proxy.get("type") == "vless"
    }


def parse_singbox_subscription(content: str) -> dict[str, Any]:
    """Parse a canonical sing-box document and validate its outbound graph.

    `sing-box check` decodes every option but resolves no outbound tags, so
    dangling group members and a final route that skips the urltest pass it.
    """
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Canonical sing-box subscription is not JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Canonical sing-box subscription must be an object")
    outbounds = parsed.get("outbounds")
    if not isinstance(outbounds, list) or not outbounds or not all(isinstance(item, dict) for item in outbounds):
        raise ValueError("Canonical sing-box subscription has no outbounds")
    tags = [outbound.get("tag") for outbound in outbounds]
    if not all(isinstance(tag, str) and tag for tag in tags) or len(set(tags)) != len(tags):
        raise ValueError("Canonical sing-box outbound tags must be nonempty and unique")
    by_tag: dict[str, dict[str, Any]] = dict(zip(tags, outbounds, strict=True))

    proxies = [outbound for outbound in outbounds if "server" in outbound]
    if not proxies:
        raise ValueError("Canonical sing-box subscription has no proxies")
    for proxy in proxies:
        server, port = proxy.get("server"), proxy.get("server_port")
        if not isinstance(server, str) or not server:
            raise ValueError("Canonical sing-box subscription contains an incomplete proxy")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("Canonical sing-box subscription contains an invalid proxy port")
        if proxy.get("type") == "vless":
            try:
                UUID(str(proxy.get("uuid", "")))
            except ValueError as exc:
                raise ValueError("Canonical sing-box subscription contains an invalid VLESS UUID") from exc

    for outbound in outbounds:
        if outbound.get("type") not in _SINGBOX_GROUP_TYPES:
            continue
        members = outbound.get("outbounds")
        if not isinstance(members, list) or not members:
            raise ValueError(f"Canonical sing-box group {outbound['tag']!r} is empty")
        if not all(isinstance(member, str) and member in by_tag for member in members):
            raise ValueError(f"Canonical sing-box group {outbound['tag']!r} references an unknown outbound")
        default = outbound.get("default")
        if default and default not in members:
            raise ValueError(f"Canonical sing-box group {outbound['tag']!r} defaults to a non-member")

    def reaches_urltest(tag: object) -> bool:
        # A selector starts on its default (else first) member, so one led by a urltest routes through it.
        outbound = by_tag.get(tag) if isinstance(tag, str) else None
        if outbound is None or outbound.get("type") not in _SINGBOX_GROUP_TYPES:
            return False
        if outbound["type"] == "urltest":
            return True
        leader = outbound.get("default") or outbound["outbounds"][0]
        return by_tag[leader].get("type") == "urltest"

    route = parsed.get("route")
    if not isinstance(route, dict) or not reaches_urltest(route.get("final")):
        raise ValueError("Canonical sing-box subscription does not route through its urltest group")
    return parsed


def singbox_endpoint_addresses(config: dict[str, Any]) -> set[str]:
    """Return endpoint addresses from a validated sing-box document."""
    return {
        outbound["server"]
        for outbound in config.get("outbounds", [])
        if isinstance(outbound, dict) and isinstance(outbound.get("server"), str) and outbound["server"]
    }


def singbox_vless_user_ids(config: dict[str, Any]) -> set[str]:
    """Return normalized VLESS UUIDs from a validated sing-box document."""
    return {
        str(UUID(str(outbound["uuid"])))
        for outbound in config.get("outbounds", [])
        if isinstance(outbound, dict) and outbound.get("type") == "vless"
    }


def engine_check_command(client_type: str, document: Path, home: Path) -> list[str]:
    """Return the argv that has a client's own engine validate one document."""
    if client_type == SINGBOX_CLIENT_TYPE:
        return ["sing-box", "check", "--disable-color", "-D", str(home), "-c", str(document)]
    if client_type == MIHOMO_CLIENT_TYPE:
        return ["mihomo", "-t", "-d", str(home), "-f", str(document)]
    raise ValueError(f"No client engine checks {client_type!r} documents")


def _run_engine(command: Sequence[str], engine: str, timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise ValueError(f"{engine} is not installed ({command[0]} is not on PATH)") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"{engine} did not finish within {timeout:g}s") from exc


def check_with_engine(
    client_type: str,
    content: str,
    workdir: Path,
    *,
    geodata_dir: Path | None = None,
    timeout: float = 60,
) -> str:
    """Have the client's own engine validate one delivered document; return its output.

    The engine's home starts with the pinned geodata only, so a document that
    makes the engine download rule lists fails instead of depending on the network.
    """
    engine = ENGINE_NAMES.get(client_type, client_type)
    home = workdir / f"{client_type}-home"
    home.mkdir(parents=True)
    if geodata_dir is not None:
        for source in sorted(geodata_dir.iterdir()):
            shutil.copy2(source, home / source.name)
    document = workdir / f"{client_type}.{'json' if client_type == SINGBOX_CLIENT_TYPE else 'yaml'}"
    document.write_text(content, encoding="utf-8")
    command = engine_check_command(client_type, document, home)
    pinned = {path.name for path in home.iterdir()}

    result = _run_engine(command, engine, timeout)
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    if result.returncode != 0:
        raise ValueError(f"{engine} rejected the delivered document (exit {result.returncode}):\n{output}")
    fetched = sorted({path.name for path in home.iterdir()} - pinned)
    if fetched:
        raise ValueError(
            f"{engine} downloaded {', '.join(fetched)} while checking; pin it in the controller image:\n{output}"
        )
    return output


def xray_vless_user_ids(config: dict[str, Any]) -> set[str]:
    """Return normalized VLESS UUIDs from canonical Xray proxy outbounds."""
    return {
        str(UUID(str(user["id"])))
        for outbound in proxy_outbounds(config)
        if outbound.get("protocol") == "vless"
        for destination in outbound.get("settings", {}).get("vnext", [])
        if isinstance(destination, dict)
        for user in destination.get("users", [])
        if isinstance(user, dict) and user.get("id")
    }


def replace_user_ids(
    config: dict[str, Any],
    replacement: str,
) -> dict[str, Any]:
    """Corrupt delivered credentials without reconstructing endpoints."""
    selected = copy.deepcopy(config)
    replaced = 0
    for outbound in proxy_outbounds(selected):
        settings = outbound.get("settings")
        if not isinstance(settings, dict):
            continue
        destinations = settings.get("vnext")
        if not isinstance(destinations, list):
            continue
        for destination in destinations:
            if not isinstance(destination, dict):
                continue
            users = destination.get("users")
            if not isinstance(users, list):
                continue
            for user in users:
                if isinstance(user, dict) and "id" in user:
                    user["id"] = replacement
                    replaced += 1
    if not replaced:
        raise ValueError("Canonical Xray subscription contains no user IDs")
    return selected


def missing_addresses(
    config: dict[str, Any],
    expected: Iterable[str],
) -> set[str]:
    """Compare expected lab endpoints with canonical delivery."""
    return set(expected) - endpoint_addresses(config)
