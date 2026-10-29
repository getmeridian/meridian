"""A node agent's side of enrollment and hello, in Python, for control-service tests."""

from __future__ import annotations

import secrets
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from cryptography.x509.oid import NameOID

from meridian.core.agent_signing import sign_proof

FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
CAPABILITIES = ["long_poll", "xray_config", "xray_users", "traffic_stats", "future_thing"]


def csr_pem(key: Ed25519PrivateKey | ec.EllipticCurvePrivateKey, common_name: str = "ignored") -> str:
    builder = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    )
    algorithm = None if isinstance(key, Ed25519PrivateKey) else hashes.SHA256()
    return builder.sign(key, algorithm).public_bytes(Encoding.PEM).decode("ascii")


class FakeNode:
    """Generates its key locally, enrolls, and signs hellos like the Rust agent."""

    def __init__(self, node_id: str = "main", fleet_id: str = FLEET_ID) -> None:
        self.node_id = node_id
        self.fleet_id = fleet_id
        self.key = Ed25519PrivateKey.generate()
        self.certificate_pem = ""

    def rotate_key(self) -> None:
        self.key = Ed25519PrivateKey.generate()

    def enroll_request(self, token: str) -> dict[str, Any]:
        return {
            "type": "enroll",
            "protocol": "meridian.agent/v1",
            "fleet_id": self.fleet_id,
            "node_id": self.node_id,
            "enrollment_token": token,
            "csr_pem": csr_pem(self.key),
            "agent_version": "0.2.0",
            "arch": "amd64",
        }

    def hello(self, issued_at: str, *, nonce: str = "", highest_fleet_epoch: int | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": "hello",
            "protocols": ["meridian.agent/v1"],
            "fleet_id": self.fleet_id,
            "node_id": self.node_id,
            "agent_version": "0.2.0",
            "arch": "amd64",
            "capabilities": CAPABILITIES,
            "highest_fleet_epoch": highest_fleet_epoch,
            "applied": None,
            "proof": {
                "certificate_pem": self.certificate_pem,
                "issued_at": issued_at,
                "nonce": nonce or secrets.token_urlsafe(24),
            },
        }
        raw = self.key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        body["proof"]["signature"] = sign_proof(body, raw)
        return body
