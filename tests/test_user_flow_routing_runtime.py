"""Pinned Xray routing keeps its DNS pass after a live identity revocation."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import threading
from contextlib import ExitStack

from meridian.config import XRAY_VERSION
from tests.test_user_flow_runtime import (
    UUID_A,
    UUID_B,
    Echo,
    EchoServer,
    port,
    start,
)
from tests.test_user_flow_runtime import (
    runtime_driver as runtime_driver,
)


def test_active_domains_still_hit_private_blocks_and_ip_egress_rules(xray_binary, runtime_driver, tmp_path):
    api, inbound_port, client_port = port(), port(), port()
    with ExitStack() as stack:
        target = stack.enter_context(EchoServer(("127.0.0.1", 0), Echo))
        threading.Thread(target=target.serve_forever, daemon=True).start()
        stack.callback(target.shutdown)
        target_port = target.server_address[1]
        allow = [{"action": "allow", "network": "tcp", "ip": ["127.0.0.0/8"]}]
        base = {
            "log": {"loglevel": "none"},
            "api": {"tag": "api", "services": ["HandlerService", "StatsService", "RoutingService"]},
            "stats": {},
            "dns": {
                "hosts": {"private.example.org": "127.0.0.1", "egress.example.org": "127.0.0.2"},
                "servers": ["127.0.0.1"],
                "queryStrategy": "UseIPv4",
            },
            "inbounds": [
                {
                    "tag": "vless",
                    "listen": "127.0.0.1",
                    "port": inbound_port,
                    "protocol": "vless",
                    "settings": {"clients": [], "decryption": "none"},
                },
                {
                    "tag": "api",
                    "listen": "127.0.0.1",
                    "port": api,
                    "protocol": "dokodemo-door",
                    "settings": {"address": "127.0.0.1"},
                },
            ],
            "outbounds": [
                {
                    "tag": "direct",
                    "protocol": "freedom",
                    "settings": {"domainStrategy": "UseIPv4", "finalRules": allow},
                },
                {
                    "tag": "egress",
                    "protocol": "freedom",
                    "settings": {"redirect": f"127.0.0.1:{target_port}", "finalRules": allow},
                },
                {"tag": "block", "protocol": "blackhole"},
            ],
            "routing": {
                "domainStrategy": "IPIfNonMatch",
                "rules": [
                    {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                    {"type": "field", "ip": ["127.0.0.2/32"], "outboundTag": "egress"},
                    {"type": "field", "ip": ["geoip:private"], "outboundTag": "block"},
                ],
            },
        }
        resources = []
        for name, source, path in [
            ("xray", xray_binary, "/usr/local/lib/netagent/netcore"),
            ("geoip", xray_binary.parent / "geoip.dat", "/usr/local/lib/netagent/geoip.dat"),
        ]:
            resources.append(
                {
                    "id": name,
                    "kind": "binary_pin",
                    "depends_on": [],
                    "name": name,
                    "version": XRAY_VERSION,
                    "sources": [f"https://assets.example.org/{name}"],
                    "size": source.stat().st_size,
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "archive": "none",
                    "member": None,
                    "install_path": path,
                    "mode": "0755" if name == "xray" else "0644",
                }
            )
        resources.extend(
            [
                {
                    "id": "core-config",
                    "kind": "xray_config",
                    "depends_on": ["xray", "geoip"],
                    "path": "/etc/netagent/core.json",
                    "config": base,
                    "api_address": f"127.0.0.1:{api}",
                },
                {
                    "id": "users",
                    "kind": "xray_users",
                    "depends_on": ["core-config"],
                    "inbound_tag": "vless",
                    "users": [
                        {"user_id": person, "uuid": credential, "flow": ""}
                        for person, credential in [("u-a", UUID_A), ("u-b", UUID_B)]
                    ],
                },
                {
                    "id": "core-unit",
                    "kind": "systemd_unit",
                    "depends_on": ["xray", "core-config", "users"],
                    "name": "netcore.service",
                    "content": "[Service]\nExecStart=/usr/local/lib/netagent/netcore run -c /etc/netagent/core.json\n",
                    "enabled": True,
                    "state": "started",
                    "restart_on": ["xray", "core-config", "users"],
                },
            ]
        )
        after = copy.deepcopy(resources)
        after[3]["users"] = after[3]["users"][1:]
        client = {
            "log": {"loglevel": "none"},
            "inbounds": [
                {"listen": "127.0.0.1", "port": client_port, "protocol": "socks", "settings": {"auth": "noauth"}}
            ],
            "outbounds": [
                {
                    "protocol": "vless",
                    "settings": {
                        "vnext": [
                            {
                                "address": "127.0.0.1",
                                "port": inbound_port,
                                "users": [{"id": UUID_B, "encryption": "none"}],
                            }
                        ]
                    },
                    "mux": {"enabled": True, "concurrency": 8},
                }
            ],
        }
        process, _ = start(stack, xray_binary, client, tmp_path, "active-client")
        assert process.poll() is None
        allow_private = tmp_path / "allow-private.json"
        permit = copy.deepcopy(base["routing"])
        permit["rules"] = permit["rules"][:-1]
        allow_private.write_text(json.dumps({"routing": permit}))
        base_routing = tmp_path / "base-routing.json"
        base_routing.write_text(json.dumps({"routing": base["routing"]}))
        fixture = tmp_path / "routing-fixture.json"
        fixture.write_text(
            json.dumps(
                {
                    "binary": str(xray_binary),
                    "geoip": str(xray_binary.parent / "geoip.dat"),
                    "root": str(tmp_path / "root"),
                    "before": resources,
                    "after": after,
                    "target": target_port,
                    "client": client_port,
                    "api": f"127.0.0.1:{api}",
                    "allow_private": str(allow_private),
                    "base_routing": str(base_routing),
                }
            )
        )
        result = subprocess.run(
            [runtime_driver, "--exact", "active_domains_keep_the_dns_routing_pass_after_revocation", "--nocapture"],
            env={**os.environ, "MERIDIAN_FLOW_ROUTING_FIXTURE": str(fixture)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
