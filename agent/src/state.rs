//! The agent's persistent state (`state.json` in the state directory, mode 0600).

use std::io::ErrorKind;
use std::path::Path;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::model::NodeStateRef;

pub const STATE_FILE: &str = "state.json";

#[derive(Serialize, Deserialize, Default, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct State {
    /// Highest fleet epoch ever accepted; older epochs are refused for good.
    pub highest_epoch: Option<u64>,
    /// The newest verified desired-state document, exactly as received.
    pub held: Option<Value>,
    /// The last document fully applied.
    pub applied: Option<NodeStateRef>,
    /// Resources applied at least once, in document order (retirement walks it backwards).
    pub ledger: Vec<LedgerEntry>,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct LedgerEntry {
    pub id: String,
    pub kind: String,
    /// The resource object last applied, kept so it can be retired later.
    pub resource: Value,
    pub applied_sha256: String,
    /// SHA-256 of what is on disk for it (installed binary, file, composed config, unit file).
    pub installed_sha256: Option<String>,
    pub changed_at: String,
}

impl State {
    /// Load state; a missing file is a fresh agent, a malformed one is refused (fail closed:
    /// forgetting the highest epoch would let an old control server roll the node back).
    pub fn load(state_dir: &Path) -> Result<State, String> {
        let path = state_dir.join(STATE_FILE);
        match std::fs::read(&path) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|err| {
                format!(
                    "{} is malformed ({err}); move it aside to start fresh",
                    path.display()
                )
            }),
            Err(err) if err.kind() == ErrorKind::NotFound => Ok(State::default()),
            Err(err) => Err(format!("cannot read {}: {err}", path.display())),
        }
    }

    pub fn save(&self, state_dir: &Path) -> Result<(), String> {
        let bytes = serde_json::to_vec(self).map_err(|err| err.to_string())?;
        std::fs::create_dir_all(state_dir)
            .map_err(|err| format!("cannot create state dir: {err}"))?;
        crate::fsutil::write_atomic(&state_dir.join(STATE_FILE), &bytes, 0o600, None)
            .map_err(|err| format!("cannot save state: {err}"))
    }

    pub fn held_ref(&self) -> Option<NodeStateRef> {
        let held = self.held.as_ref()?;
        Some(NodeStateRef {
            fleet_epoch: held.get("fleet_epoch")?.as_u64()?,
            revision: held.get("revision")?.as_u64()?,
            sha256: held.get("sha256")?.as_str()?.to_string(),
        })
    }
}
