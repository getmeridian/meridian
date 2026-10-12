#!/usr/bin/env python3
"""Snapshot active bindings, then prove the ones a removal dropped are gone remotely.

``snapshot FILE`` records every active managed binding. ``verify FILE --expect
KIND...`` diffs that snapshot against the current cluster: each binding that is
no longer active must be absent on its server or in the panel, and every
expected kind must appear among them.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

from meridian.cluster import ClusterConfig
from meridian.compiler.names import service_username
from meridian.compiler.routing import edge_id
from meridian.config import SERVER_PROFILES_FILE
from meridian.reconciler.server_render import artifact_token, firewall_marker, realm_service_name
from meridian.remnawave import MeridianPanel
from meridian.servers import ServerRegistry

NODE_DIR = "/opt/remnanode"


def _active() -> dict[str, dict[str, str]]:
    cluster = ClusterConfig.load()
    return {
        binding.logical_id: {
            "kind": binding.resource_kind,
            "remote_id": binding.remote_id,
            "server_ref": binding.server_ref,
        }
        for binding in cluster.managed_bindings.values()
        if binding.active
    }


def _ssh_succeeds(host: str, command: str) -> bool:
    result = subprocess.run(["ssh", f"root@{host}", command], capture_output=True, text=True, timeout=60)
    if result.returncode == 255:
        raise RuntimeError(f"SSH to {host} failed: {result.stderr.strip()}")
    return result.returncode == 0


def _server_leftover(logical_id: str, kind: str, host: str) -> str:
    token = shlex.quote(f"meridian-v4-{artifact_token(logical_id)}")
    if kind == "realm_hop":
        service = shlex.quote(realm_service_name(logical_id))
        if _ssh_succeeds(host, f"systemctl cat {service} >/dev/null 2>&1 || systemctl is-active --quiet {service}"):
            return f"Realm unit {realm_service_name(logical_id)} still exists"
    elif kind == "firewall_rule":
        marker = shlex.quote(firewall_marker(logical_id))
        if _ssh_succeeds(host, f"ufw status | grep -qF -- {marker}"):
            return f"UFW rule {firewall_marker(logical_id)} still present"
    elif kind == "nginx_artifact":
        listing = "ls /etc/nginx/stream.d /etc/nginx/sites-available /etc/nginx/sites-enabled 2>/dev/null"
        if _ssh_succeeds(host, f"{listing} | grep -qF -- {token}"):
            return f"nginx file {token} still present"
    elif kind == "node_runtime":
        if _ssh_succeeds(host, "docker ps -a --format '{{.Names}}' | grep -qx remnawave-node"):
            return "remnawave-node container still exists"
        if _ssh_succeeds(host, f"test -e {NODE_DIR}"):
            return f"{NODE_DIR} still exists"
    return ""


def _panel_leftover(panel: MeridianPanel, logical_id: str, kind: str, remote_id: str) -> str:
    if kind == "node_binding" and remote_id and panel.get_node(remote_id) is not None:
        return f"panel Node {remote_id} still exists"
    if kind == "config_profile" and remote_id and panel.get_config_profile(remote_id) is not None:
        return f"panel config profile {remote_id} still exists"
    if kind == "host" and remote_id and panel.get_host(remote_id) is not None:
        return f"panel Host {remote_id} still exists"
    if kind == "internal_squad" and remote_id and panel.get_internal_squad(remote_id) is not None:
        return f"panel internal squad {remote_id} still exists"
    if kind == "external_squad" and remote_id and panel.get_external_squad(remote_id) is not None:
        return f"panel external squad {remote_id} still exists"
    if kind == "service_user":
        _, _, edge = logical_id.partition(":")
        gateway_ref, _, exit_ref = edge.partition(":")
        username = service_username(edge_id(gateway_ref, exit_ref))
        if panel.get_user(username) is not None:
            return f"panel service User {username} still exists"
    return ""


def verify(snapshot: Path, expected_kinds: list[str]) -> int:
    before: dict[str, dict[str, str]] = json.loads(snapshot.read_text(encoding="utf-8"))
    after = _active()
    vanished = {logical_id: info for logical_id, info in before.items() if logical_id not in after}
    kinds = sorted({info["kind"] for info in vanished.values()})
    print(f"    retired {len(vanished)} bindings: {', '.join(kinds)}")
    missing = sorted(set(expected_kinds) - set(kinds))
    if missing:
        print("FAIL: removal did not retire any " + ", ".join(missing))
        return 1

    registry = ServerRegistry(SERVER_PROFILES_FILE)
    cluster = ClusterConfig.load()
    failures: list[str] = []
    with MeridianPanel(cluster.panel.url, cluster.panel.api_token) as panel:
        for logical_id, info in sorted(vanished.items()):
            leftover = _panel_leftover(panel, logical_id, info["kind"], info["remote_id"])
            if not leftover and info["server_ref"]:
                entry = registry.find(info["server_ref"])
                if entry is None:
                    failures.append(f"{logical_id}: saved server {info['server_ref']} vanished from the shelf")
                    continue
                leftover = _server_leftover(logical_id, info["kind"], entry.host)
            if leftover:
                failures.append(f"{logical_id}: {leftover}")
    for failure in failures:
        print(f"FAIL: {failure}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["snapshot", "verify"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--expect", action="append", default=[], help="Kind that must be retired; repeatable")
    args = parser.parse_args()
    if args.mode == "snapshot":
        args.path.write_text(json.dumps(_active(), indent=2, sort_keys=True), encoding="utf-8")
        return 0
    return verify(args.path, args.expect)


if __name__ == "__main__":
    sys.exit(main())
