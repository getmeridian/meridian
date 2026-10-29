//! The node's credentials: an Ed25519 key generated here, its certificate, the
//! pinned CA, and the service signing certificate.
//!
//! Everything lives in one `identity.json` (0600) in the state directory, so a
//! renewal replaces key and certificate together. The private key never leaves
//! the node: enrollment and renewal send only a CSR.

use std::fmt;
use std::io::ErrorKind;
use std::path::Path;
use std::str::FromStr;

use base64::Engine as _;
use base64::engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD};
use ed25519_dalek::{Signature, Signer, SigningKey, VerifyingKey};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use x509_cert::Certificate;
use x509_cert::der::asn1::BitString;
use x509_cert::der::oid::ObjectIdentifier;
use x509_cert::der::pem::LineEnding;
use x509_cert::der::{DecodePem, Encode, EncodePem};
use x509_cert::ext::pkix::name::GeneralName;
use x509_cert::ext::pkix::{BasicConstraints, SubjectAltName};
use x509_cert::name::Name;
use x509_cert::request::{CertReq, CertReqInfo, Version};
use x509_cert::spki::{AlgorithmIdentifierOwned, SubjectPublicKeyInfoOwned};

use crate::jcs;
use crate::verify::Trust;

pub const IDENTITY_FILE: &str = "identity.json";
pub const HELLO_CONTEXT: &[u8] = b"meridian.agent/v1 hello";
const ED25519: ObjectIdentifier = ObjectIdentifier::new_unwrap("1.3.101.112");

pub fn agent_uri(fleet_id: &str, node_id: &str) -> String {
    format!("meridian-agent://{fleet_id}/{node_id}")
}

pub fn service_uri(fleet_id: &str) -> String {
    format!("meridian-service://{fleet_id}")
}

/// Fill a buffer from the operating system's CSPRNG.
pub fn random_bytes<const N: usize>() -> Result<[u8; N], String> {
    let mut bytes = [0u8; N];
    getrandom::getrandom(&mut bytes).map_err(|err| format!("no system randomness: {err}"))?;
    Ok(bytes)
}

/// A fresh Ed25519 node key.
pub fn generate_key() -> Result<SigningKey, String> {
    Ok(SigningKey::from_bytes(&random_bytes::<32>()?))
}

/// A fresh opaque token (a hello nonce): 24 random bytes in base64url.
pub fn random_token() -> Result<String, String> {
    Ok(URL_SAFE_NO_PAD.encode(random_bytes::<24>()?))
}

/// A fresh lowercase UUIDv4 (a counter epoch).
pub fn random_uuid() -> Result<String, String> {
    let mut b = random_bytes::<16>()?;
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    let hex = jcs::hex(&b);
    Ok(format!(
        "{}-{}-{}-{}-{}",
        &hex[..8],
        &hex[8..12],
        &hex[12..16],
        &hex[16..20],
        &hex[20..]
    ))
}

/// A PKCS#10 request for `key`; the service ignores the subject and sets the SAN itself.
pub fn csr_pem(key: &SigningKey, node_id: &str) -> Result<String, String> {
    let der_err = |err: x509_cert::der::Error| format!("cannot build the CSR: {err}");
    let info = CertReqInfo {
        version: Version::V1,
        subject: Name::from_str(&format!("CN={node_id}")).map_err(der_err)?,
        public_key: SubjectPublicKeyInfoOwned {
            algorithm: ed25519_algorithm(),
            subject_public_key: BitString::from_bytes(key.verifying_key().as_bytes())
                .map_err(der_err)?,
        },
        attributes: Default::default(),
    };
    let signature = key.sign(&info.to_der().map_err(der_err)?);
    let request = CertReq {
        info,
        algorithm: ed25519_algorithm(),
        signature: BitString::from_bytes(&signature.to_bytes()).map_err(der_err)?,
    };
    request.to_pem(LineEnding::LF).map_err(der_err)
}

fn ed25519_algorithm() -> AlgorithmIdentifierOwned {
    AlgorithmIdentifierOwned {
        oid: ED25519,
        parameters: None,
    }
}

/// The fleet CA, accepted only when its DER hashes to the bootstrap's `ca_sha256`.
#[derive(Clone, Debug)]
pub struct Ca {
    pub pem: String,
    pub sha256: String,
    certificate: Certificate,
}

impl Ca {
    pub fn pinned(pem: &str, ca_sha256: &str) -> Result<Ca, String> {
        let certificate = parse(pem, "CA")?;
        let der = certificate
            .to_der()
            .map_err(|err| format!("CA certificate: {err}"))?;
        let sha256 = jcs::sha256_hex(&der);
        if sha256 != ca_sha256 {
            return Err("the CA certificate does not match the pinned ca_sha256".into());
        }
        Ok(Ca {
            pem: pem.to_string(),
            sha256,
            certificate,
        })
    }

    /// Check that the CA signed `certificate` (Ed25519 only).
    fn issued(&self, certificate: &Certificate, what: &str) -> Result<(), String> {
        let tbs = &certificate.tbs_certificate;
        if certificate.signature_algorithm.oid != ED25519 || tbs.signature.oid != ED25519 {
            return Err(format!("the {what} certificate is not signed with Ed25519"));
        }
        if tbs.issuer != self.certificate.tbs_certificate.subject {
            return Err(format!(
                "the {what} certificate is not issued by the fleet CA"
            ));
        }
        let ca_key = ed25519_key(&self.certificate, "CA")?;
        let signature = certificate
            .signature
            .as_bytes()
            .and_then(|raw| Signature::from_slice(raw).ok())
            .ok_or(format!("the {what} certificate signature is malformed"))?;
        let message = tbs
            .to_der()
            .map_err(|err| format!("{what} certificate: {err}"))?;
        ca_key
            .verify_strict(&message, &signature)
            .map_err(|_| format!("the {what} certificate is not signed by the fleet CA"))
    }
}

/// The service signing certificate: its key verifies desired state, its hash is the `key_id`.
#[derive(Clone, Debug)]
pub struct ServiceCert {
    pub pem: String,
    pub public_key: [u8; 32],
    pub key_id: String,
}

impl ServiceCert {
    /// Accept only a CA-issued, unexpired, non-CA certificate naming this fleet's service,
    /// so a node certificate (also CA-issued) can never pass for it.
    pub fn check(pem: &str, ca: &Ca, fleet_id: &str, now: u64) -> Result<ServiceCert, String> {
        let certificate = parse(pem, "service")?;
        ca.issued(&certificate, "service")?;
        if is_ca(&certificate) {
            return Err("the service certificate is a CA certificate".into());
        }
        if uris(&certificate) != [service_uri(fleet_id)] {
            return Err("the service certificate does not name this fleet's service".into());
        }
        let not_after = certificate
            .tbs_certificate
            .validity
            .not_after
            .to_unix_duration()
            .as_secs();
        if now > not_after {
            return Err("the service certificate has expired".into());
        }
        let der = certificate
            .to_der()
            .map_err(|err| format!("service certificate: {err}"))?;
        Ok(ServiceCert {
            pem: pem.to_string(),
            public_key: ed25519_key(&certificate, "service")?.to_bytes(),
            key_id: jcs::sha256_hex(&der),
        })
    }
}

/// Check a node certificate the service issued: CA-signed, naming this node, for `key`.
pub fn check_node_certificate(
    pem: &str,
    ca: &Ca,
    fleet_id: &str,
    node_id: &str,
    key: &VerifyingKey,
) -> Result<(), String> {
    let certificate = parse(pem, "node")?;
    ca.issued(&certificate, "node")?;
    if is_ca(&certificate) {
        return Err("the node certificate is a CA certificate".into());
    }
    if uris(&certificate) != [agent_uri(fleet_id, node_id)] {
        return Err("the node certificate names another node".into());
    }
    if ed25519_key(&certificate, "node")? != *key {
        return Err("the node certificate is not for this node's key".into());
    }
    Ok(())
}

fn parse(pem: &str, what: &str) -> Result<Certificate, String> {
    Certificate::from_pem(pem.as_bytes())
        .map_err(|err| format!("the {what} certificate does not parse: {err}"))
}

fn ed25519_key(certificate: &Certificate, what: &str) -> Result<VerifyingKey, String> {
    let spki = &certificate.tbs_certificate.subject_public_key_info;
    if spki.algorithm.oid != ED25519 {
        return Err(format!(
            "the {what} certificate does not carry an Ed25519 key"
        ));
    }
    let raw: [u8; 32] = spki
        .subject_public_key
        .as_bytes()
        .and_then(|bytes| bytes.try_into().ok())
        .ok_or(format!("the {what} certificate key is malformed"))?;
    VerifyingKey::from_bytes(&raw).map_err(|_| format!("the {what} certificate key is invalid"))
}

fn uris(certificate: &Certificate) -> Vec<String> {
    match certificate.tbs_certificate.get::<SubjectAltName>() {
        Ok(Some((_, san))) => san
            .0
            .iter()
            .filter_map(|name| match name {
                GeneralName::UniformResourceIdentifier(uri) => Some(uri.to_string()),
                _ => None,
            })
            .collect(),
        _ => Vec::new(),
    }
}

fn is_ca(certificate: &Certificate) -> bool {
    matches!(
        certificate.tbs_certificate.get::<BasicConstraints>(),
        Ok(Some((_, constraints))) if constraints.ca
    ) || certificate
        .tbs_certificate
        .get::<BasicConstraints>()
        .is_err()
}

/// What a `NodeProof` signature covers: context, newline, canonical hello without `proof.signature`.
pub fn proof_signed_bytes(hello: &Value) -> Result<Vec<u8>, String> {
    let mut unsigned = hello.clone();
    if let Some(proof) = unsigned.get_mut("proof").and_then(Value::as_object_mut) {
        proof.remove("signature");
    }
    let mut out = HELLO_CONTEXT.to_vec();
    out.push(b'\n');
    out.extend(jcs::canonical(&unsigned)?);
    Ok(out)
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct IdentityFile {
    node_key: String,
    certificate_pem: String,
    ca_certificate_pem: String,
    service_certificate_pem: String,
}

/// Enrolled credentials. `Debug` never prints the key.
#[derive(Clone)]
pub struct Identity {
    pub key: SigningKey,
    pub certificate_pem: String,
    pub ca: Ca,
    pub service: ServiceCert,
}

impl fmt::Debug for Identity {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Identity")
            .field("ca", &self.ca.sha256)
            .field("service_key_id", &self.service.key_id)
            .finish_non_exhaustive()
    }
}

impl Identity {
    /// Load stored credentials. A missing file is a node that never enrolled, and a malformed
    /// one is refused (fail closed); credentials of another CA or node load as `None`, so the
    /// agent needs a fresh enrollment token.
    pub fn load(
        state_dir: &Path,
        ca_sha256: &str,
        fleet_id: &str,
        node_id: &str,
        now: u64,
    ) -> Result<Option<Identity>, String> {
        let path = state_dir.join(IDENTITY_FILE);
        let bytes = match std::fs::read(&path) {
            Ok(bytes) => bytes,
            Err(err) if err.kind() == ErrorKind::NotFound => return Ok(None),
            Err(err) => return Err(format!("cannot read {}: {err}", path.display())),
        };
        let file: IdentityFile = serde_json::from_slice(&bytes).map_err(|err| {
            format!(
                "{} is malformed ({err}); move it aside and enroll again",
                path.display()
            )
        })?;
        let seed: [u8; 32] = STANDARD
            .decode(&file.node_key)
            .ok()
            .and_then(|raw| raw.try_into().ok())
            .ok_or(format!("{} holds a malformed node key", path.display()))?;
        let key = SigningKey::from_bytes(&seed);
        let checked = Ca::pinned(&file.ca_certificate_pem, ca_sha256).and_then(|ca| {
            check_node_certificate(
                &file.certificate_pem,
                &ca,
                fleet_id,
                node_id,
                &key.verifying_key(),
            )?;
            let service = ServiceCert::check(&file.service_certificate_pem, &ca, fleet_id, now)?;
            Ok((ca, service))
        });
        match checked {
            Ok((ca, service)) => Ok(Some(Identity {
                key,
                certificate_pem: file.certificate_pem,
                ca,
                service,
            })),
            Err(reason) => {
                crate::info!("stored credentials do not fit the bootstrap ({reason})");
                Ok(None)
            }
        }
    }

    pub fn save(&self, state_dir: &Path) -> Result<(), String> {
        let file = IdentityFile {
            node_key: STANDARD.encode(self.key.to_bytes()),
            certificate_pem: self.certificate_pem.clone(),
            ca_certificate_pem: self.ca.pem.clone(),
            service_certificate_pem: self.service.pem.clone(),
        };
        let bytes = serde_json::to_vec(&file).map_err(|err| err.to_string())?;
        std::fs::create_dir_all(state_dir)
            .map_err(|err| format!("cannot create state dir: {err}"))?;
        crate::fsutil::write_atomic(&state_dir.join(IDENTITY_FILE), &bytes, 0o600, None)
            .map_err(|err| format!("cannot save credentials: {err}"))
    }

    pub fn trust(&self, fleet_id: &str, node_id: &str) -> Trust {
        Trust {
            fleet_id: fleet_id.to_string(),
            node_id: node_id.to_string(),
            public_key: self.service.public_key,
            key_id: self.service.key_id.clone(),
        }
    }

    /// Sign a hello body: returns the base64 `proof.signature`.
    pub fn sign_proof(&self, hello: &Value) -> Result<String, String> {
        Ok(STANDARD.encode(self.key.sign(&proof_signed_bytes(hello)?).to_bytes()))
    }
}
