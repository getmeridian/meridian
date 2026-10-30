//! Desired-state verification and ordering.
//!
//! The signature and hashes are checked over the document exactly as it
//! arrived (parsed into a `Value`, never re-serialized from typed structs);
//! only a verified document is deserialized further.

use std::collections::HashSet;

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD;
use ed25519_dalek::{Signature, VerifyingKey};
use serde_json::Value;

use crate::jcs;
use crate::model::{DesiredDocument, NodeStateRef, PROTOCOL, Resource};

pub const DESIRED_STATE_CONTEXT: &[u8] = b"meridian.agent/v1 desired-state";
pub const MAX_RESOURCES: usize = 1024;

/// Whose documents the agent accepts: this node's, signed by the service certificate's key.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Trust {
    pub fleet_id: String,
    pub node_id: String,
    pub public_key: [u8; 32],
    /// SHA-256 of the service certificate's DER.
    pub key_id: String,
}

/// A desired-state document whose signature, hashes, and identity checked out.
#[derive(Clone, Debug)]
pub struct Verified {
    pub reference: NodeStateRef,
    pub resources: Vec<Resource>,
}

/// Exactly what the service signs: context line, newline, canonical JSON without `signature`.
pub fn signed_bytes(document: &Value) -> Result<Vec<u8>, String> {
    signed_bytes_in_context(document, DESIRED_STATE_CONTEXT)
}

pub(crate) fn signed_bytes_in_context(document: &Value, context: &[u8]) -> Result<Vec<u8>, String> {
    let mut unsigned = document
        .as_object()
        .ok_or("desired state is not a JSON object")?
        .clone();
    unsigned.remove("signature");
    let mut out = context.to_vec();
    out.push(b'\n');
    out.extend(jcs::canonical(&Value::Object(unsigned))?);
    Ok(out)
}

/// Check the signature member against the service certificate's key.
pub fn verify_signature(
    document: &Value,
    public_key: &[u8; 32],
    key_id: &str,
) -> Result<(), String> {
    verify_signature_in_context(document, public_key, key_id, DESIRED_STATE_CONTEXT)
}

pub(crate) fn verify_signature_in_context(
    document: &Value,
    public_key: &[u8; 32],
    key_id: &str,
    context: &[u8],
) -> Result<(), String> {
    let member = document
        .get("signature")
        .and_then(Value::as_object)
        .ok_or("desired state is unsigned")?;
    if member.get("key_id").and_then(Value::as_str) != Some(key_id) {
        return Err("desired state is signed by an unknown key".into());
    }
    let encoded = member
        .get("signature")
        .and_then(Value::as_str)
        .ok_or("signature is missing")?;
    let raw = STANDARD
        .decode(encoded)
        .map_err(|_| "signature is not base64".to_string())?;
    let signature =
        Signature::from_slice(&raw).map_err(|_| "signature is not 64 bytes".to_string())?;
    let key = VerifyingKey::from_bytes(public_key)
        .map_err(|_| "service public key is invalid".to_string())?;
    key.verify_strict(&signed_bytes_in_context(document, context)?, &signature)
        .map_err(|_| "signature does not verify".to_string())
}

/// Verify a desired-state document received from the service (or reloaded from state).
pub fn verify_desired(document: &Value, trust: &Trust) -> Result<Verified, String> {
    jcs::check_no_floats(document)?;
    verify_signature(document, &trust.public_key, &trust.key_id)?;
    let typed: DesiredDocument = serde_json::from_value(document.clone())
        .map_err(|err| format!("desired state is malformed: {err}"))?;
    if typed.protocol != PROTOCOL {
        return Err(format!("unsupported protocol {:?}", typed.protocol));
    }
    if typed.fleet_id != trust.fleet_id || typed.node_id != trust.node_id {
        return Err("desired state is for another fleet or node".into());
    }
    if typed.fleet_epoch == 0 || typed.revision == 0 {
        return Err("fleet_epoch and revision start at 1".into());
    }
    let resources_value = document.get("resources").ok_or("resources are missing")?;
    if jcs::canonical_sha256(resources_value)? != typed.sha256 {
        return Err("sha256 does not match resources".into());
    }
    if typed.resources.len() > MAX_RESOURCES {
        return Err("too many resources".into());
    }
    let mut seen = HashSet::new();
    let mut resources = Vec::with_capacity(typed.resources.len());
    for raw in &typed.resources {
        let resource = Resource::parse(raw)?;
        if let Some(dep) = resource
            .depends_on
            .iter()
            .find(|dep| !seen.contains(dep.as_str()))
        {
            return Err(format!(
                "resource {} depends on {dep}, which is not an earlier resource",
                resource.id
            ));
        }
        if !seen.insert(resource.id.clone()) {
            return Err(format!("duplicate resource id {}", resource.id));
        }
        resources.push(resource);
    }
    Ok(Verified {
        reference: NodeStateRef {
            fleet_epoch: typed.fleet_epoch,
            revision: typed.revision,
            sha256: typed.sha256,
        },
        resources,
    })
}

/// What to do with a verified document, given what the agent already holds.
#[derive(Debug, PartialEq, Eq)]
pub enum Admission {
    /// New content: hold and apply it.
    Apply,
    /// Same content under a newer revision: record it without applying.
    RecordRevision,
    /// The document already held.
    AlreadyHeld,
    Refuse(String),
}

pub fn admit(
    highest_epoch: Option<u64>,
    held: Option<&NodeStateRef>,
    new: &NodeStateRef,
) -> Admission {
    if let Some(highest) = highest_epoch
        && new.fleet_epoch < highest
    {
        return Admission::Refuse(format!(
            "epoch {} is below the highest accepted epoch {highest}",
            new.fleet_epoch
        ));
    }
    let Some(held) = held else {
        return Admission::Apply;
    };
    let new_key = (new.fleet_epoch, new.revision);
    let held_key = (held.fleet_epoch, held.revision);
    if new_key < held_key {
        return Admission::Refuse(format!(
            "revision {}/{} is older than the held {}/{}",
            new.fleet_epoch, new.revision, held.fleet_epoch, held.revision
        ));
    }
    if new_key == held_key {
        return if new.sha256 == held.sha256 {
            Admission::AlreadyHeld
        } else {
            Admission::Refuse(format!(
                "revision {}/{} repeats with different content",
                new.fleet_epoch, new.revision
            ))
        };
    }
    if new.sha256 == held.sha256 {
        Admission::RecordRevision
    } else {
        Admission::Apply
    }
}
