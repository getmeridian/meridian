"""Write cross-language vectors for the Rust agent's canonical JSON, PKI, and signature checks.

Run from the repository root:

    uv run python agent/tests/fixtures/make_fixtures.py

A fleet CA, service signing certificate, and node certificate come from
`meridian.control.pki` with fixed test keys (never used outside these fixtures);
the document is a valid `DesiredNodeState` signed under the service certificate's
`key_id`, and the hello is a valid `HelloRequest` whose proof the node key signed.
Strings carry non-ASCII and characters that need escaping, so the Rust canonical
form must match Python's byte for byte. Certificate serials are random, so a
regenerated file differs; the checks do not depend on them.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

from meridian.control.pki import create_ca, issue_node_certificate, issue_service_certificate, pem
from meridian.core.agent_desired import AgentResourceSpec, DesiredNodeState
from meridian.core.agent_protocol import AgentBootstrap, HelloRequest
from meridian.core.agent_signing import (
    DESIRED_STATE_CONTEXT,
    certificate_key_id,
    proof_signed_bytes,
    public_key,
    sign,
    sign_proof,
    signed_bytes,
)
from meridian.core.agent_types import canonical_json, canonical_sha256

OUT = Path(__file__).with_name("vectors.json")
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
CA_KEY = bytes([3] * 32)
PRIVATE_KEY = bytes(range(32))
NODE_KEY = bytes([5] * 32)
FLEET_ID = "0b6f3c1e-8f6a-4d2b-9c41-7a5e2d9f0c13"
NODE_ID = "exit-1"
UNIT_TEXT = (
    '[Unit]\nDescription=Netcore éè ✓ \U0001f512 "quoted" back\\slash\ttab\u0001\u001f\u007f \n'
    "[Service]\nExecStart=/usr/local/lib/netagent/netcore run -c /etc/netagent/core.json\n"
)
FILE_CONTENT = "token = über\n".encode()


def _resources() -> list[dict]:
    raw = [
        {
            "kind": "binary_pin",
            "id": "pin:xray",
            "depends_on": [],
            "name": "xray",
            "version": "26.7.11",
            "sources": ["https://github.com/XTLS/Xray-core/releases/download/v26.7.11/Xray-linux-64.zip", "service"],
            "size": 12345,
            "sha256": "ab" * 32,
            "archive": "zip",
            "member": "xray",
            "install_path": "/usr/local/lib/netagent/netcore",
            "mode": "0755",
        },
        {
            "kind": "file",
            "id": "file:extra",
            "depends_on": [],
            "path": "/etc/netagent/extra.conf",
            "mode": "0640",
            "owner": "root",
            "group": "root",
            "size": len(FILE_CONTENT),
            "sha256": hashlib.sha256(FILE_CONTENT).hexdigest(),
            "content_b64": base64.b64encode(FILE_CONTENT).decode(),
            "sensitive": True,
        },
        {
            "kind": "xray_config",
            "id": "xray:config",
            "depends_on": ["pin:xray"],
            "path": "/etc/netagent/core.json",
            "api_address": "127.0.0.1:10085",
            "config": {
                "log": {"loglevel": "warning", "note": 'café «» "q" \\ \n\r\t\b\f \u0000'},
                "inbounds": [
                    {
                        "tag": "vless-reality",
                        "port": 443,
                        "protocol": "vless",
                        "settings": {"clients": [], "decryption": "none"},
                    }
                ],
                "outbounds": [{"protocol": "freedom", "tag": "direct"}],
            },
        },
        {
            "kind": "xray_users",
            "id": "users:vless-reality",
            "depends_on": ["xray:config"],
            "inbound_tag": "vless-reality",
            "users": [
                {"user_id": "u-alpha", "uuid": "3f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00", "flow": "xtls-rprx-vision"}
            ],
        },
        {
            "kind": "systemd_unit",
            "id": "unit:netcore",
            "depends_on": ["xray:config", "users:vless-reality"],
            "name": "netcore.service",
            "content": UNIT_TEXT,
            "enabled": True,
            "state": "started",
            "restart_on": ["xray:config", "users:vless-reality"],
        },
        {
            "kind": "firewall_rule",
            "id": "fw:443",
            "depends_on": [],
            "port": 443,
            "protocol": "tcp",
            "allow_from": [],
        },
    ]
    return [AgentResourceSpec.model_validate(item).model_dump(mode="json", by_alias=True) for item in raw]


def _node_csr() -> str:
    key = Ed25519PrivateKey.from_private_bytes(NODE_KEY)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, NODE_ID)])
    csr = x509.CertificateSigningRequestBuilder().subject_name(name).sign(key, None)
    return csr.public_bytes(Encoding.PEM).decode("ascii")


def main() -> None:
    ca_der = create_ca(CA_KEY, NOW)
    service_der = issue_service_certificate(CA_KEY, ca_der, FLEET_ID, public_key(PRIVATE_KEY), NOW)
    node_certificate = issue_node_certificate(CA_KEY, ca_der, FLEET_ID, NODE_ID, _node_csr(), NOW)
    service_key_id = certificate_key_id(service_der)
    resources = _resources()
    unsigned = {
        "protocol": "meridian.agent/v1",
        "fleet_id": FLEET_ID,
        "node_id": NODE_ID,
        "fleet_epoch": 3,
        "revision": 7,
        "sha256": canonical_sha256(resources),
        "issued_at": "2026-10-29T12:00:00Z",
        "resources": resources,
    }
    document = {**unsigned, "signature": sign(unsigned, DESIRED_STATE_CONTEXT, PRIVATE_KEY, service_key_id)}
    DesiredNodeState.model_validate(document)

    tampered = copy.deepcopy(document)
    tampered["issued_at"] = "2026-10-29T12:00:01Z"

    bootstrap = AgentBootstrap(
        service_url="https://198.51.100.10/s3cr3t-path",
        fleet_id=FLEET_ID,
        node_id=NODE_ID,
        enrollment_token="t" * 43,
        token_expires_at="2026-10-29T12:30:00Z",
        ca_sha256=hashlib.sha256(ca_der).hexdigest(),
        tls_pin_sha256=None,
    )
    hello = {
        "type": "hello",
        "protocols": ["meridian.agent/v1"],
        "fleet_id": FLEET_ID,
        "node_id": NODE_ID,
        "agent_version": "0.2.0",
        "arch": "amd64",
        "capabilities": ["long_poll", "traffic_stats"],
        "highest_fleet_epoch": 3,
        "applied": {"fleet_epoch": 3, "revision": 7, "sha256": canonical_sha256(resources)},
        "proof": {"certificate_pem": node_certificate.pem, "issued_at": "2026-10-29T12:00:00Z", "nonce": "n" * 32},
    }
    hello["proof"]["signature"] = sign_proof(hello, NODE_KEY)
    HelloRequest.model_validate(hello)
    escapes = {"z": "é✓\U0001f512", "a": '\u0000\u0001\b\f\n\r\t\u001f\u007f"\\/ ', "m": [1, -2, 9007199254740991]}
    vectors = {
        "bootstrap": bootstrap.model_dump(mode="json"),
        "ca_certificate_pem": pem(ca_der),
        "service_certificate_pem": pem(service_der),
        "node_certificate_pem": node_certificate.pem,
        "node_key_hex": NODE_KEY.hex(),
        "now": int(NOW.timestamp()),
        "key_id": service_key_id,
        "hello": hello,
        "hello_signed_bytes_hex": proof_signed_bytes(hello).hex(),
        "document": document,
        "tampered": tampered,
        "signed_bytes_hex": signed_bytes(document, DESIRED_STATE_CONTEXT).hex(),
        "resource_sha256": {item["id"]: canonical_sha256(item) for item in resources},
        "canonical": {"value": escapes, "bytes_hex": canonical_json(escapes).hex()},
    }
    # ensure_ascii=True: the file itself uses \u escapes, so parsing must restore the characters.
    OUT.write_text(json.dumps(vectors, indent=2, ensure_ascii=True) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
