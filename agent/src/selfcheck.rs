//! `netagent check`: can this build take over the node as it stands?
//!
//! The deployer runs a staged binary with `check` before swapping it in. It loads
//! the bootstrap, the credentials, the state, and the traffic store exactly as
//! `run` would, and verifies the held document, but writes nothing and opens no
//! connection, so a build that cannot read the node's files never replaces one that can.

use std::path::Path;

use serde_json::{Value, json};

use crate::bootstrap::Bootstrap;
use crate::identity::Identity;
use crate::state::State;
use crate::stats::TrafficStore;
use crate::timeutil::now_unix;
use crate::verify::verify_desired;

/// One JSON object: `version`, `node_id`, `enrolled`, and whether the held document still verifies.
pub fn self_check(bootstrap: &Path, state_dir: &Path, allow_http: bool) -> Result<Value, String> {
    let bootstrap = Bootstrap::load(bootstrap, allow_http)?;
    let state = State::load(state_dir)?;
    TrafficStore::load(state_dir)?;
    let identity = Identity::load(
        state_dir,
        &bootstrap.ca_sha256,
        &bootstrap.fleet_id,
        &bootstrap.node_id,
        now_unix(),
    )?;
    let held_verifies = match (&state.held, &identity) {
        (Some(held), Some(identity)) => Some(
            verify_desired(
                held,
                &identity.trust(&bootstrap.fleet_id, &bootstrap.node_id),
            )
            .is_ok(),
        ),
        _ => None,
    };
    Ok(json!({
        "version": crate::VERSION,
        "node_id": bootstrap.node_id,
        "enrolled": identity.is_some(),
        "held_verifies": held_verifies,
    }))
}
