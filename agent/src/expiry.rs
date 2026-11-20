//! Local evaluation of the last signed deadlines; the original document is kept untouched.
use std::io::ErrorKind;
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

use crate::model::{Resource, Spec};

#[derive(Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Clock {
    /// Last authenticated hello's difference between service UTC and this host's UTC.
    pub offset: i64,
    /// Once a deadline has been reached, a backward clock step cannot reinstate it.
    pub floor: u64,
}

impl Clock {
    pub fn load(dir: &Path) -> Result<Self, String> {
        match std::fs::read(dir.join("expiry-clock.json")) {
            Ok(bytes) => serde_json::from_slice(&bytes).map_err(|_| "Invalid expiry clock.".into()),
            Err(e) if e.kind() == ErrorKind::NotFound => Ok(Self::default()),
            Err(_) => Err("Cannot read the expiry clock.".into()),
        }
    }

    pub fn save(&self, dir: &Path) -> Result<(), String> {
        crate::fsutil::write_atomic(
            &dir.join("expiry-clock.json"),
            &serde_json::to_vec(self).unwrap(),
            0o600,
            None,
        )
        .map_err(|_| "Cannot persist the expiry clock.".into())
    }

    pub fn now(&self) -> u64 {
        crate::timeutil::now_unix()
            .saturating_add_signed(self.offset)
            .max(self.floor)
    }
}

/// Time left until a wall-clock deadline. Recompute after every blocking call / clock adjustment.
pub(crate) fn time_left(local: u64) -> Duration {
    Duration::from_secs(local).saturating_sub(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default(),
    )
}

/// Filter the effective accounts in place, recording the exact applied resource in the ledger.
/// Resource.hash still names the original SIGNED resource for protocol observations; a new
/// signed document replaces this projection and is the only way to renew an expired account.
pub(crate) fn project(resources: &mut [Resource], now: u64) -> (Option<u64>, Option<u64>) {
    let mut next: Option<u64> = None;
    let mut latest: Option<u64> = None;
    for resource in resources {
        let mut keep = |expiry: Option<u64>| match expiry {
            Some(t) if t <= now => {
                latest = Some(latest.map_or(t, |old| old.max(t)));
                false
            }
            Some(t) => {
                next = Some(next.map_or(t, |old| old.min(t)));
                true
            }
            None => true,
        };
        let filtered = match &mut resource.spec {
            Spec::XrayUsers(spec) => {
                let count = spec.users.len();
                spec.users.retain(|u| keep(u.expires_unix));
                count != spec.users.len()
            }
            Spec::Hysteria2Users(spec) => {
                let count = spec.users.len();
                spec.users.retain(|u| keep(u.expires_unix));
                count != spec.users.len()
            }
            _ => false,
        };
        if filtered {
            resource.expiry_filtered = true;
            resource.raw["users"]
                .as_array_mut()
                .expect("typed accounts")
                .retain(|u| u["expires_unix"].as_u64().is_none_or(|t| t > now));
        }
    }
    (next, latest)
}
