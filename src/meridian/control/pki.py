"""The fleet's internal CA: Ed25519 X.509 certificates for the service and its node agents.

The CA certificate is what every node pins (`AgentBootstrap.ca_sha256`). It
issues one service certificate, whose key signs desired state and commands,
and one node certificate per enrollment or renewal, from a CSR whose key was
generated on the node. The two kinds are told apart by their URI SAN, so a
node key can never pass for the service's.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.x509.oid import NameOID

CA_LIFETIME = timedelta(days=365 * 20)
SERVICE_LIFETIME = timedelta(days=365 * 10)
NODE_LIFETIME = timedelta(days=90)
NODE_RENEW_AFTER = timedelta(days=60)
# Certificates start slightly in the past so a node whose clock lags a little still accepts them.
BACKDATE = timedelta(minutes=10)


class CertificateError(Exception):
    """A certificate or CSR was refused; `code` is the agent error code to answer with."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class IssuedCertificate:
    der: bytes
    sha256: str
    serial: str
    not_after: datetime
    renew_after: datetime

    @property
    def pem(self) -> str:
        return pem(self.der)


@dataclass(frozen=True)
class NodeCertificate:
    """A node certificate that chains to the CA and names the expected node."""

    sha256: str
    public_key: bytes
    not_after: datetime


def agent_uri(fleet_id: str, node_id: str) -> str:
    return f"meridian-agent://{fleet_id}/{node_id}"


def service_uri(fleet_id: str) -> str:
    return f"meridian-service://{fleet_id}"


def pem(der: bytes) -> str:
    return x509.load_der_x509_certificate(der).public_bytes(Encoding.PEM).decode("ascii")


def der_sha256(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def create_ca(ca_key: bytes, now: datetime) -> bytes:
    """Return the DER of a fresh self-signed CA certificate for the raw Ed25519 key."""
    private = Ed25519PrivateKey.from_private_bytes(ca_key)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fleet-ca")])
    certificate = (
        _builder(name, name, private.public_key(), now, CA_LIFETIME)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_key_usage(cert_sign=True), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(private.public_key()), critical=False)
        .sign(private, algorithm=None)
    )
    return certificate.public_bytes(Encoding.DER)


def issue_service_certificate(
    ca_key: bytes, ca_der: bytes, fleet_id: str, service_public: bytes, now: datetime
) -> bytes:
    """Return the DER of the service signing certificate for a raw Ed25519 public key."""
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fleet-service")])
    return _issue(
        ca_key,
        ca_der,
        subject,
        Ed25519PublicKey.from_public_bytes(service_public),
        service_uri(fleet_id),
        now,
        SERVICE_LIFETIME,
    ).der


def issue_node_certificate(
    ca_key: bytes, ca_der: bytes, fleet_id: str, node_id: str, csr_pem: str, now: datetime
) -> IssuedCertificate:
    """Check a node's CSR and return its certificate; the CSR's subject is ignored."""
    try:
        csr = x509.load_pem_x509_csr(csr_pem.encode("ascii"))
    except ValueError as exc:
        raise CertificateError("invalid_request", "The CSR is not a PKCS#10 request.") from exc
    key = csr.public_key()
    if not isinstance(key, Ed25519PublicKey):
        raise CertificateError("invalid_request", "The CSR must carry an Ed25519 key.")
    if not csr.is_signature_valid:
        raise CertificateError("invalid_request", "The CSR signature does not verify.")
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_id)])
    return _issue(ca_key, ca_der, subject, key, agent_uri(fleet_id, node_id), now, NODE_LIFETIME)


def check_node_certificate(
    ca_der: bytes, certificate_pem: str, fleet_id: str, node_id: str, now: datetime
) -> NodeCertificate:
    """Verify a node certificate's chain, validity, and SAN; revocation is the caller's (it keeps the records)."""
    try:
        certificate = x509.load_pem_x509_certificate(certificate_pem.encode("ascii"))
    except ValueError as exc:
        raise CertificateError("unauthenticated", "The node certificate does not parse.") from exc
    ca = x509.load_der_x509_certificate(ca_der)
    try:
        certificate.verify_directly_issued_by(ca)
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise CertificateError("unauthenticated", "The node certificate is not issued by this fleet.") from exc
    if now > certificate.not_valid_after_utc:
        raise CertificateError("credential_expired", "The node certificate has expired.")
    if now < certificate.not_valid_before_utc:
        raise CertificateError("clock_skew", "The node certificate is not valid yet.")
    if _uris(certificate) != [agent_uri(fleet_id, node_id)]:
        raise CertificateError("unauthenticated", "The node certificate names another node.")
    key = certificate.public_key()
    if not isinstance(key, Ed25519PublicKey):
        raise CertificateError("unauthenticated", "The node certificate does not carry an Ed25519 key.")
    der = certificate.public_bytes(Encoding.DER)
    return NodeCertificate(
        sha256=der_sha256(der),
        public_key=key.public_bytes(Encoding.Raw, PublicFormat.Raw),
        not_after=certificate.not_valid_after_utc,
    )


def not_after(der: bytes) -> datetime:
    return x509.load_der_x509_certificate(der).not_valid_after_utc


def _issue(
    ca_key: bytes,
    ca_der: bytes,
    subject: x509.Name,
    key: Ed25519PublicKey,
    uri: str,
    now: datetime,
    lifetime: timedelta,
) -> IssuedCertificate:
    ca = x509.load_der_x509_certificate(ca_der)
    signer = Ed25519PrivateKey.from_private_bytes(ca_key)
    builder = (
        _builder(subject, ca.subject, key, now, lifetime)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(_key_usage(cert_sign=False), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.UniformResourceIdentifier(uri)]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(signer.public_key()), critical=False)
    )
    certificate = builder.sign(signer, algorithm=None)
    der = certificate.public_bytes(Encoding.DER)
    return IssuedCertificate(
        der=der,
        sha256=der_sha256(der),
        serial=format(certificate.serial_number, "x"),
        not_after=certificate.not_valid_after_utc,
        renew_after=_floor(now) + NODE_RENEW_AFTER,
    )


def _builder(
    subject: x509.Name, issuer: x509.Name, key: Ed25519PublicKey, now: datetime, lifetime: timedelta
) -> x509.CertificateBuilder:
    start = _floor(now)
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(start - BACKDATE)
        .not_valid_after(start + lifetime)
    )


def _key_usage(*, cert_sign: bool) -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=not cert_sign,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=cert_sign,
        crl_sign=cert_sign,
        encipher_only=False,
        decipher_only=False,
    )


def _uris(certificate: x509.Certificate) -> list[str]:
    try:
        san = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return []
    return san.get_values_for_type(x509.UniformResourceIdentifier)


def _floor(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(microsecond=0)
