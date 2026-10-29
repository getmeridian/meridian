//! The skeleton bootstrap file (`NodeBootstrap` in `meridian.control.models`).

use std::fmt;
use std::path::Path;

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD;
use serde::Deserialize;

use crate::model::PROTOCOL;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct BootstrapFile {
    protocol: String,
    service_url: String,
    fleet_id: String,
    node_id: String,
    node_token: String,
    service_public_key: String,
}

/// Loaded bootstrap. `Debug` never prints the token.
#[derive(Clone)]
pub struct Bootstrap {
    pub service_url: String,
    pub fleet_id: String,
    pub node_id: String,
    pub node_token: String,
    pub public_key: [u8; 32],
    /// Lowercase hex SHA-256 of the raw public key.
    pub key_id: String,
}

impl fmt::Debug for Bootstrap {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Bootstrap")
            .field("fleet_id", &self.fleet_id)
            .field("node_id", &self.node_id)
            .field("key_id", &self.key_id)
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
        let file: BootstrapFile = serde_json::from_slice(bytes)
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
        if file.node_token.len() < 32
            || !file
                .node_token
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        {
            return Err("bootstrap node_token is not a base64url token".into());
        }
        let key = STANDARD
            .decode(&file.service_public_key)
            .map_err(|_| "bootstrap service_public_key is not base64".to_string())?;
        let public_key: [u8; 32] = key
            .try_into()
            .map_err(|_| "bootstrap service_public_key is not 32 bytes".to_string())?;
        Ok(Bootstrap {
            service_url: file.service_url,
            fleet_id: file.fleet_id,
            node_id: file.node_id,
            node_token: file.node_token,
            key_id: crate::jcs::sha256_hex(&public_key),
            public_key,
        })
    }
}
