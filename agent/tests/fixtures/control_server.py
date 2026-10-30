"""Run the real control service on loopback for the Rust agent's cross-language round trip.

`agent/tests/service.rs` starts it from the repository root:

    uv run python agent/tests/fixtures/control_server.py WORKDIR

It serves `ControlServer` on 127.0.0.1 with one node spec (Xray with one managed
VLESS inbound) and one user, then prints one JSON line: the agent bootstrap (a
fresh enrollment token, the CA pin, and a plain-HTTP loopback `service_url` the
agent accepts only in tests) and the user's `user_id`. Each line on stdin is a
command answered with one JSON line: `traffic` (`GET /api/traffic`), `node` (the
node's status), or `user NAME` (adds a user); end of input stops the service. The fake
Xray binary the spec pins is seeded into the service's real blob store, which the
agent fetches through `blobs/{sha256}`.
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from pathlib import Path

# A supplied interpreter may be an editable install of another worktree.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from meridian.control.blobs import BlobStore
from meridian.control.enrollment import token_sha256
from meridian.control.models import NodeSpec, users_resource_id
from meridian.control.server import ControlServer
from meridian.control.service import ControlService

FLEET_ID = "0b6f3c1e-8f6a-4d2b-9c41-7a5e2d9f0c13"
NODE_ID = "exit-1"
TAG = "vless-reality"
XRAY = b"\x7fELF fake xray"


def _spec() -> NodeSpec:
    config = {
        "log": {"loglevel": "warning"},
        "inbounds": [{"tag": TAG, "port": 443, "protocol": "vless", "settings": {"clients": [], "decryption": "none"}}],
        "outbounds": [{"protocol": "freedom"}],
    }
    return NodeSpec.model_validate(
        {
            "resources": [
                {
                    "id": "pin:xray",
                    "kind": "binary_pin",
                    "name": "xray",
                    "version": "26.7.11",
                    "sources": ["service"],
                    "size": len(XRAY),
                    "sha256": hashlib.sha256(XRAY).hexdigest(),
                    "archive": "none",
                    "member": None,
                    "install_path": "/usr/local/lib/netagent/netcore",
                    "mode": "0755",
                },
                {
                    "id": "xray:config",
                    "depends_on": ["pin:xray"],
                    "kind": "xray_config",
                    "path": "/etc/netagent/core.json",
                    "config": config,
                    "api_address": "127.0.0.1:10085",
                },
                {
                    "id": "unit:netcore",
                    "depends_on": ["xray:config", users_resource_id(TAG)],
                    "kind": "systemd_unit",
                    "name": "netcore.service",
                    "content": "[Service]\nExecStart=/usr/local/lib/netagent/netcore run -c /etc/netagent/core.json\n",
                    "enabled": True,
                    "state": "started",
                    "restart_on": ["xray:config", users_resource_id(TAG)],
                },
            ],
            "managed_inbounds": [{"tag": TAG, "protocol": "vless", "flow": "xtls-rprx-vision"}],
        }
    )


def main() -> None:
    workdir = Path(sys.argv[1])
    service = ControlService(workdir / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    blobs = BlobStore(workdir / "blobs")
    blobs.put(XRAY)
    admin = token_sha256("admin-token-for-tests-only-0123456789")
    server = ControlServer(("127.0.0.1", 0), service, admin, blobs)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    user = service.put_user("alice")
    service.put_node_spec(NODE_ID, _spec())
    grant = service.grant_enrollment(NODE_ID)
    bootstrap = {
        "protocol": "meridian.agent/v1",
        "service_url": f"http://127.0.0.1:{server.server_address[1]}",
        "fleet_id": FLEET_ID,
        "node_id": NODE_ID,
        "enrollment_token": grant.enrollment_token,
        "token_expires_at": grant.token_expires_at,
        "ca_sha256": grant.ca_sha256,
        "tls_pin_sha256": None,
    }
    print(json.dumps({"bootstrap": bootstrap, "user_id": user.user_id}), flush=True)
    for line in sys.stdin:
        command = line.strip()
        if command == "traffic":
            print(json.dumps(service.traffic.summary().model_dump(mode="json")), flush=True)
        elif command == "node":
            print(json.dumps(service.node_status(NODE_ID).model_dump(mode="json")), flush=True)
        elif command.startswith("user "):
            print(json.dumps(service.put_user(command.split()[1]).model_dump(mode="json")), flush=True)
    server.shutdown()
    server.server_close()


if __name__ == "__main__":
    main()
