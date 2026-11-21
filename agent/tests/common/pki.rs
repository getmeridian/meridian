//! A test fleet CA in Rust: the same certificate shapes `meridian.control.pki` issues
//! (Ed25519, URI SAN, BasicConstraints), built field by field with x509-cert.

use std::str::FromStr;
use std::time::Duration;

use ed25519_dalek::{SigningKey, VerifyingKey};
use meridian_agent::jcs;
use x509_cert::Certificate;
use x509_cert::builder::{Builder, CertificateBuilder, profile::BuilderProfile};
use x509_cert::certificate::TbsCertificate;
use x509_cert::der::asn1::{BitString, Ia5String, OctetString, UtcTime};
use x509_cert::der::oid::{AssociatedOid, ObjectIdentifier};
use x509_cert::der::pem::LineEnding;
use x509_cert::der::{DecodePem, Encode, EncodePem};
use x509_cert::ext::Extension;
use x509_cert::ext::pkix::name::GeneralName;
use x509_cert::ext::pkix::{BasicConstraints, SubjectAltName};
use x509_cert::name::Name;
use x509_cert::request::CertReq;
use x509_cert::serial_number::SerialNumber;
use x509_cert::spki::{
    AlgorithmIdentifierOwned, SubjectPublicKeyInfoOwned, SubjectPublicKeyInfoRef,
};
use x509_cert::time::{Time, Validity};

use super::FLEET_ID;

const ED25519: ObjectIdentifier = ObjectIdentifier::new_unwrap("1.3.101.112");
/// 2026-10-29T00:00:00Z: certificates are valid from here...
const NOT_BEFORE: u64 = 1_793_232_000;
/// ...until 2046.
const NOT_AFTER: u64 = NOT_BEFORE + 20 * 365 * 86_400;

fn algorithm() -> AlgorithmIdentifierOwned {
    AlgorithmIdentifierOwned {
        oid: ED25519,
        parameters: None,
    }
}

fn time(secs: u64) -> Time {
    Time::UtcTime(UtcTime::from_unix_duration(Duration::from_secs(secs)).unwrap())
}

fn extension<T: AssociatedOid + Encode>(value: &T, critical: bool) -> Extension {
    Extension {
        extn_id: T::OID,
        critical,
        extn_value: OctetString::new(value.to_der().unwrap()).unwrap(),
    }
}

// The service uses the same basic constraints and URI SANs throughout enrollment.
struct TestCertificate {
    issuer: Name,
    subject: Name,
    extensions: Vec<Extension>,
}

impl BuilderProfile for TestCertificate {
    fn get_issuer(&self, _: &Name) -> Name {
        self.issuer.clone()
    }
    fn get_subject(&self) -> Name {
        self.subject.clone()
    }
    fn build_extensions(
        &self,
        _: SubjectPublicKeyInfoRef<'_>,
        _: SubjectPublicKeyInfoRef<'_>,
        _: &TbsCertificate,
    ) -> x509_cert::builder::Result<Vec<Extension>> {
        Ok(self.extensions.clone())
    }
}

/// One certificate: `uri` adds a SAN, `ca` the CA basic constraint.
pub fn certificate(
    issuer_key: &SigningKey,
    issuer: &str,
    subject: &str,
    subject_key: &VerifyingKey,
    uri: Option<&str>,
    ca: bool,
    not_after: u64,
) -> String {
    let mut extensions = vec![extension(
        &BasicConstraints {
            ca,
            path_len_constraint: ca.then_some(0),
        },
        true,
    )];
    if let Some(uri) = uri {
        let san = SubjectAltName(vec![GeneralName::UniformResourceIdentifier(
            Ia5String::new(uri).unwrap(),
        )]);
        extensions.push(extension(&san, false));
    }
    CertificateBuilder::new(
        TestCertificate {
            issuer: Name::from_str(&format!("CN={issuer}")).unwrap(),
            subject: Name::from_str(&format!("CN={subject}")).unwrap(),
            extensions,
        },
        SerialNumber::new(&jcs::sha256_hex(subject.as_bytes()).as_bytes()[..16]).unwrap(),
        Validity::new(time(NOT_BEFORE), time(not_after)),
        SubjectPublicKeyInfoOwned {
            algorithm: algorithm(),
            subject_public_key: BitString::from_bytes(subject_key.as_bytes()).unwrap(),
        },
    )
    .unwrap()
    .build::<_, ed25519_dalek::Signature>(issuer_key)
    .unwrap()
    .to_pem(LineEnding::LF)
    .unwrap()
}

pub fn der_sha256(pem: &str) -> String {
    jcs::sha256_hex(
        &Certificate::from_pem(pem.as_bytes())
            .unwrap()
            .to_der()
            .unwrap(),
    )
}

/// The CA and service signing certificate of the test fleet, from fixed seeds.
#[derive(Clone)]
pub struct TestPki {
    pub ca_key: SigningKey,
    pub ca_pem: String,
    pub service_key: SigningKey,
    pub service_pem: String,
}

impl TestPki {
    pub fn new() -> TestPki {
        TestPki::with_service_seed(7)
    }

    /// The same CA with another service key (a re-certified signing key).
    pub fn with_service_seed(seed: u8) -> TestPki {
        let ca_key = SigningKey::from_bytes(&[4u8; 32]);
        let ca_pem = certificate(
            &ca_key,
            "fleet-ca",
            "fleet-ca",
            &ca_key.verifying_key(),
            None,
            true,
            NOT_AFTER,
        );
        let service_key = SigningKey::from_bytes(&[seed; 32]);
        let service_pem = certificate(
            &ca_key,
            "fleet-ca",
            "fleet-service",
            &service_key.verifying_key(),
            Some(&format!("meridian-service://{FLEET_ID}")),
            false,
            NOT_AFTER,
        );
        TestPki {
            ca_key,
            ca_pem,
            service_key,
            service_pem,
        }
    }

    pub fn ca_sha256(&self) -> String {
        der_sha256(&self.ca_pem)
    }

    pub fn key_id(&self) -> String {
        der_sha256(&self.service_pem)
    }

    /// Certify the key in a node's CSR (checking the CSR's own signature first).
    pub fn issue_node(&self, node_id: &str, csr_pem: &str) -> Result<String, String> {
        let request = CertReq::from_pem(csr_pem.as_bytes()).map_err(|err| err.to_string())?;
        let raw: [u8; 32] = request
            .info
            .public_key
            .subject_public_key
            .as_bytes()
            .and_then(|b| b.try_into().ok())
            .ok_or("bad CSR key")?;
        let key = VerifyingKey::from_bytes(&raw).map_err(|err| err.to_string())?;
        let signature = ed25519_dalek::Signature::from_slice(
            request.signature.as_bytes().ok_or("bad CSR signature")?,
        )
        .map_err(|err| err.to_string())?;
        key.verify_strict(&request.info.to_der().unwrap(), &signature)
            .map_err(|_| "the CSR signature does not verify".to_string())?;
        Ok(certificate(
            &self.ca_key,
            "fleet-ca",
            node_id,
            &key,
            Some(&format!("meridian-agent://{FLEET_ID}/{node_id}")),
            false,
            NOT_BEFORE + 90 * 86_400,
        ))
    }
}
