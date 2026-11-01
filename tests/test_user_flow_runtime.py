"""Real pinned QUIC and VLESS mux sessions, with no requests outside loopback."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import socket
import socketserver
import subprocess
import threading
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from meridian.config import XRAY_VERSION

ROOT = Path(__file__).resolve().parents[1]
UUID_A = "3f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00"
UUID_B = "4f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00"
UUID_NEW = "5f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00"


@pytest.fixture(scope="module")
def runtime_driver(xray_binary, tmp_path_factory):
    cargo = shutil.which("cargo") or str(Path.home() / ".cargo/bin/cargo")
    if not Path(cargo).is_file():
        pytest.skip("the real agent flow test also needs cargo")
    env = dict(os.environ)
    env.setdefault("CARGO_TARGET_DIR", str(tmp_path_factory.mktemp("flow-agent-target")))
    env["PATH"] = str(Path(cargo).parent) + os.pathsep + env["PATH"]
    built = subprocess.run(
        [cargo, "test", "--offline", "--test", "xray_runtime", "--no-run", "--message-format=json"],
        cwd=ROOT / "agent",
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert built.returncode == 0, built.stderr
    artifacts = [json.loads(line) for line in built.stdout.splitlines() if line.startswith("{")]
    executable = next(
        item["executable"]
        for item in artifacts
        if item.get("reason") == "compiler-artifact"
        and item.get("executable")
        and item["target"]["name"] == "xray_runtime"
    )
    return executable


class Echo(socketserver.BaseRequestHandler):
    def handle(self):
        while data := self.request.recv(4096):
            self.request.sendall(data)


class EchoServer(socketserver.ThreadingTCPServer):
    daemon_threads = True


def port(kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET, kind) as reserve:
        reserve.bind(("127.0.0.1", 0))
        return reserve.getsockname()[1]


def email(person, credential):
    return person + ".c" + hashlib.sha256(credential.encode()).hexdigest()[:32]


def certificate(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "vpn.example.org")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("vpn.example.org")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    certificate_file, key_file = tmp_path / "certificate.pem", tmp_path / "key.pem"
    certificate_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return {"certificateFile": str(certificate_file), "keyFile": str(key_file)}, cert.fingerprint(hashes.SHA256()).hex()


def start(stack, binary, config, tmp_path, name):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(config))
    output = stack.enter_context((tmp_path / f"{name}.log").open("w+"))
    process = subprocess.Popen([str(binary), "run", "-c", str(path)], stdout=output, stderr=subprocess.STDOUT)

    def stop():
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    stack.callback(stop)
    return process, path


@pytest.mark.parametrize("protocol", ["hysteria2", "vless-mux"])
@pytest.mark.parametrize(
    "rotation,guard_failure",
    [(False, False), (True, False), (True, True)],
    ids=["remove", "rotate", "failed-guard-rotation"],
)
def test_live_sessions_lose_only_the_revoked_identitys_new_streams(
    xray_binary, runtime_driver, tmp_path, protocol, rotation, guard_failure
):
    api, inbound_port = port(), port(socket.SOCK_DGRAM if protocol == "hysteria2" else socket.SOCK_STREAM)
    client_ports = [port() for _ in range(3)]
    tag = "hy2" if protocol == "hysteria2" else "vless"
    identities = [("u-a", UUID_A), ("u-b", UUID_B)]
    inbound = {
        "tag": tag,
        "listen": "127.0.0.1",
        "port": inbound_port,
        "protocol": "hysteria" if protocol == "hysteria2" else "vless",
        "settings": {"clients": [], **({"version": 2} if protocol == "hysteria2" else {"decryption": "none"})},
    }
    if protocol == "hysteria2":
        tls_certificate, certificate_pin = certificate(tmp_path)
        inbound["streamSettings"] = {
            "network": "hysteria",
            "security": "tls",
            "hysteriaSettings": {"version": 2},
            "tlsSettings": {"alpn": ["h3"], "certificates": [tls_certificate]},
        }
    base = {
        "log": {"loglevel": "none"},
        "api": {"tag": "api", "services": ["HandlerService", "StatsService", "RoutingService"]},
        "stats": {},
        "inbounds": [
            inbound,
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
                "settings": {"finalRules": [{"action": "allow", "network": "tcp", "ip": ["127.0.0.1/32"]}]},
            },
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": {
            "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                {"type": "field", "ip": ["127.0.0.1/32"], "outboundTag": "direct"},
                {"type": "field", "network": "tcp,udp", "outboundTag": "direct"},
            ]
        },
    }
    server = copy.deepcopy(base)
    for person, credential in identities:
        server["inbounds"][0]["settings"]["clients"].append(
            {"email": email(person, credential), "auth" if protocol == "hysteria2" else "id": credential}
        )
    resources = [
        {
            "id": "core-bin",
            "kind": "binary_pin",
            "depends_on": [],
            "name": "xray",
            "version": XRAY_VERSION,
            "sources": ["https://assets.example.org/xray"],
            "size": xray_binary.stat().st_size,
            "sha256": hashlib.sha256(xray_binary.read_bytes()).hexdigest(),
            "archive": "none",
            "member": None,
            "install_path": "/usr/local/lib/netagent/netcore",
            "mode": "0755",
        },
        {
            "id": "core-config",
            "kind": "xray_config",
            "depends_on": ["core-bin"],
            "path": "/etc/netagent/core.json",
            "config": base,
            "api_address": f"127.0.0.1:{api}",
        },
        {
            "id": "users",
            "kind": "hysteria2_users" if protocol == "hysteria2" else "xray_users",
            "depends_on": ["core-config"],
            "inbound_tag": tag,
            "users": [],
        },
        {
            "id": "core-unit",
            "kind": "systemd_unit",
            "depends_on": ["core-bin", "core-config", "users"],
            "name": "netcore.service",
            "content": "[Service]\nExecStart=/usr/local/lib/netagent/netcore run -c /etc/netagent/core.json\n",
            "enabled": True,
            "state": "started",
            "restart_on": ["core-bin", "core-config", "users"],
        },
    ]

    def users(pairs):
        return [
            {
                "user_id": person,
                **({"auth": credential} if protocol == "hysteria2" else {"uuid": credential, "flow": ""}),
            }
            for person, credential in pairs
        ]

    resources[2]["users"] = users(identities)
    after = copy.deepcopy(resources)
    after[2]["users"] = users([("u-a", UUID_NEW), ("u-b", UUID_B)] if rotation else [("u-b", UUID_B)])
    with ExitStack() as stack:
        target = stack.enter_context(EchoServer(("127.0.0.1", 0), Echo))
        threading.Thread(target=target.serve_forever, daemon=True).start()
        stack.callback(target.shutdown)
        processes = []
        for index, credential in enumerate([UUID_A, UUID_B, UUID_NEW]):
            outbound = {
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": "127.0.0.1",
                            "port": inbound_port,
                            "users": [{"id": credential, "encryption": "none"}],
                        }
                    ]
                },
                "mux": {"enabled": True, "concurrency": 8},
            }
            if protocol == "hysteria2":
                outbound = {
                    "protocol": "hysteria",
                    "settings": {"address": "127.0.0.1", "port": inbound_port, "version": 2},
                    "streamSettings": {
                        "network": "hysteria",
                        "security": "tls",
                        "hysteriaSettings": {"version": 2, "auth": credential},
                        "tlsSettings": {
                            "serverName": "vpn.example.org",
                            "pinnedPeerCertSha256": certificate_pin,
                            "alpn": ["h3"],
                        },
                    },
                }
            config = {
                "log": {"loglevel": "none"},
                "inbounds": [
                    {
                        "tag": "socks",
                        "listen": "127.0.0.1",
                        "port": client_ports[index],
                        "protocol": "socks",
                        "settings": {"auth": "noauth"},
                    }
                ],
                "outbounds": [outbound],
            }
            processes.append(start(stack, xray_binary, config, tmp_path, f"client-{index}")[0])
        assert all(process.poll() is None for process in processes), "the pinned runtime rejected a client fixture"
        restore, routing = tmp_path / "restore.json", tmp_path / "base-routing.json"
        restore_inbound = copy.deepcopy(server["inbounds"][0])
        restore_inbound["settings"]["clients"] = restore_inbound["settings"]["clients"][:1]
        restore.write_text(json.dumps({"inbounds": [restore_inbound]}))
        routing.write_text(json.dumps({"routing": base["routing"]}))
        fixture = tmp_path / "fixture.json"
        fixture.write_text(
            json.dumps(
                {
                    "binary": str(xray_binary),
                    "root": str(tmp_path / "root"),
                    "api": f"127.0.0.1:{api}",
                    "before": resources,
                    "after": after,
                    "tag": tag,
                    "email_a": email("u-a", UUID_A),
                    "credential_a": UUID_A,
                    "restore": str(restore),
                    "base_routing": str(routing),
                    "target": target.server_address[1],
                    "client_a": client_ports[0],
                    "client_b": client_ports[1],
                    "client_new": client_ports[2],
                    "rotation": rotation,
                    "guard_failure": guard_failure,
                }
            )
        )
        result = subprocess.run(
            [runtime_driver, "--nocapture"],
            env={**os.environ, "MERIDIAN_FLOW_FIXTURE": str(fixture)},
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
