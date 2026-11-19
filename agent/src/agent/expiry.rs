//! Deadline scheduling is independent of session availability, long polls and reconnect backoff.
use std::time::Duration;

use super::Agent;
use crate::expiry;

impl Agent<'_> {
    pub(super) fn project_deadlines(&mut self) {
        let (next, latest) = expiry::project(&mut self.resources, self.expiry_clock.now());
        self.next_expiry = next;
        self.arm_deadline();
        if let Some(reached) = latest
            && reached > self.expiry_clock.floor
        {
            self.expiry_clock.floor = reached;
            if let Err(err) = self.expiry_clock.save(&self.opts.state_dir) {
                // A failed disk write is never a reason to keep granting access in RAM.
                crate::warn!("{err}");
            }
        }
    }

    pub(super) fn retry_pending_expiry(&mut self) {
        if self.observations.iter().any(|o| {
            o.error
                .as_ref()
                .is_some_and(|e| e.code == "offline_expiry_pending")
        }) || (self.flow_error.is_some() && self.resources.iter().any(|r| r.expiry_filtered))
        {
            let retry = self.expiry_clock.now().saturating_add(5);
            self.next_expiry = Some(self.next_expiry.unwrap_or(retry).min(retry));
            self.arm_deadline();
        }
    }

    pub(super) fn arm_deadline(&self) {
        self.client.set_deadline(self.local_deadline());
    }

    fn local_deadline(&self) -> Option<u64> {
        self.next_expiry
            .map(|t| t.saturating_add_signed(self.expiry_clock.offset.saturating_neg()))
    }

    pub(super) fn before_expiry(&self, wait: Duration) -> Duration {
        self.local_deadline()
            .map_or(wait, |deadline| wait.min(expiry::time_left(deadline)))
    }

    pub(super) fn expiry_due(&self) -> bool {
        self.next_expiry
            .is_some_and(|t| t <= self.expiry_clock.now())
    }

    pub(super) fn enforce_due(&mut self) {
        if self.expiry_due()
            && let Err(err) = self.apply_held()
        {
            crate::info!("could not apply local expiry: {err}");
        }
    }
}
