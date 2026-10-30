//! The bootstrap file SSH writes (`AgentBootstrap` in `meridian.core.agent_protocol`).
//!
//! It names the service, the node, and the pinned CA. A file that carries an
//! enrollment token asks for a fresh enrollment; once enrolled, the agent
//! rewrites it with the token fields set to null.

use std::fmt;
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::model::PROTOCOL;

/// Loaded bootstrap. `Debug` never prints the token.
#[derive(Serialize, Deserialize, Clone, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Bootstrap {
    pub protocol: String,
    pub service_url: String,
    pub fleet_id: String,
    pub node_id: String,
    pub enrollment_token: Option<String>,
    pub token_expires_at: Option<String>,
    pub ca_sha256: String,
    pub tls_pin_sha256: Option<String>,
}

impl fmt::Debug for Bootstrap {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Bootstrap")
            .field("fleet_id", &self.fleet_id)
            .field("node_id", &self.node_id)
            .field("ca_sha256", &self.ca_sha256)
            .field("enrolling", &self.enrollment_token.is_some())
            .finish_non_exhaustive()
    }
}

impl Bootstrap {
    pub fn load(path: &Path, allow_http: bool) -> Result<Bootstrap, String> {
        let bytes = std::fs::read(path)
            .map_err(|err| format!("cannot read bootstrap {}: {err}", path.display()))?;
        Bootstrap::from_json(&bytes, allow_http)
    }

    pub fn from_json(bytes: &[u8], allow_http: bool) -> Result<Bootstrap, String> {
        let file: Bootstrap = serde_json::from_slice(bytes)
            .map_err(|err| format!("bootstrap is malformed: {err}"))?;
        if file.protocol != PROTOCOL {
            return Err(format!(
                "bootstrap protocol {:?} is not {PROTOCOL}",
                file.protocol
            ));
        }
        let scheme_ok = file.service_url.starts_with("https://")
            || (allow_http && file.service_url.starts_with("http://"));
        if !scheme_ok || file.service_url.ends_with('/') {
            return Err("bootstrap service_url must be https:// without a trailing slash".into());
        }
        if let Some(token) = &file.enrollment_token
            && !is_opaque_token(token)
        {
            return Err("bootstrap enrollment_token is not a base64url token".into());
        }
        if !crate::jcs::is_sha256_hex(&file.ca_sha256) {
            return Err("bootstrap ca_sha256 is not a lowercase hex SHA-256".into());
        }
        if file.tls_pin_sha256.is_some() {
            // Fail closed: a pinned self-signed certificate needs a custom verifier this agent lacks.
            return Err(
                "bootstrap tls_pin_sha256 is not supported by this agent; serve a WebPKI certificate"
                    .into(),
            );
        }
        Ok(file)
    }

    /// Null the spent enrollment token in memory and on disk (atomic, 0600).
    pub fn spend_token(&mut self, path: &Path) -> Result<(), String> {
        self.enrollment_token = None;
        self.token_expires_at = None;
        let mut bytes = serde_json::to_vec_pretty(self).map_err(|err| err.to_string())?;
        bytes.push(b'\n');
        crate::fsutil::write_atomic(path, &bytes, 0o600, None)
            .map_err(|err| format!("cannot rewrite bootstrap: {err}"))
    }
}

/// The contract's `OpaqueToken`: 32 to 256 base64url characters.
pub fn is_opaque_token(token: &str) -> bool {
    (32..=256).contains(&token.len())
        && token
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}
