//! Keep the on-disk authentication current before a live change can race a core restart.

use std::fs;
use std::path::Path;

use serde_json::Value;

use crate::apply::{Engine, Failure};
use crate::fsutil::{self, host_path};
use crate::info;
use crate::model::{Resource, XrayConfigSpec};
use crate::xray::LiveChange;
use crate::xray_revocations::core_invocation;

pub(crate) struct LiveApply<'a> {
    pub spec: &'a XrayConfigSpec,
    pub resources: &'a [Resource],
    pub composed: &'a Value,
    pub binary: &'a str,
    pub previous: &'a Value,
    pub changes: &'a [LiveChange],
    pub temporary: &'a Path,
}

impl Engine<'_> {
    pub(crate) fn apply_live_config(&self, change: LiveApply<'_>) -> Result<bool, Failure> {
        let pending = |message: String| Failure::new("flow_guard_pending", message);
        let result = (|| {
            let invocation = core_invocation(self.exec).map_err(pending)?;
            match self.guard_live_users(
                change.binary,
                &change.spec.api_address,
                change.composed,
                change.resources,
                Some(change.previous),
            ) {
                Err(failure) if !failure.can_apply_authentication => {
                    return Err(pending(failure.message));
                }
                Err(_) => {
                    info!("revocation rules pending; applying authentication changes and retrying")
                }
                Ok(()) => {}
            }
            if core_invocation(self.exec).map_err(pending)? != invocation {
                return Err(pending(
                    "The core restarted before the credential change. Retry the apply.".into(),
                ));
            }
            let destination = host_path(&self.root, &change.spec.path);
            self.keep_previous(&destination);
            fsutil::commit(change.temporary, &destination, 0o600, None)
                .map_err(|error| Failure::io(&error, "cannot install config"))?;
            // A new instance may have read the old file immediately before this
            // commit. Reconciliation must restart it from the now-current file.
            if core_invocation(self.exec).ok().as_deref() != Some(&invocation) {
                return Ok(false);
            }
            match self.apply_live(change.binary, &change.spec.api_address, change.changes) {
                Ok(()) => Ok(true),
                Err(message) => {
                    // An instance born after the commit already loaded every new
                    // account. rmu then legitimately reports zero removed users.
                    if core_invocation(self.exec).ok().as_deref() != Some(&invocation)
                        && crate::xray_health::wait_ready(&self.root, self.exec, change.resources)
                            .is_ok()
                    {
                        return Ok(true);
                    }
                    info!("xray api: {message}; restarting xray instead");
                    Ok(false)
                }
            }
        })();
        if result.is_err() {
            let _ = fs::remove_file(change.temporary);
        }
        result
    }
}
