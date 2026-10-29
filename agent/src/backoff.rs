//! Retry backoff for service outages.

use std::time::{Duration, SystemTime, UNIX_EPOCH};

pub const MAX_BACKOFF: Duration = Duration::from_secs(60);

/// Exponential backoff with jitter: 1 s doubling to 60 s, never below `retry_after_seconds`.
pub struct Backoff {
    attempt: u32,
    seed: u64,
}

impl Default for Backoff {
    fn default() -> Self {
        Backoff::new()
    }
}

impl Backoff {
    pub fn new() -> Backoff {
        let seed = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos() as u64)
            .unwrap_or(0x9e37_79b9_7f4a_7c15)
            | 1;
        Backoff { attempt: 0, seed }
    }

    pub fn reset(&mut self) {
        self.attempt = 0;
    }

    pub fn next(&mut self, retry_after: Option<u64>) -> Duration {
        let ceiling_ms = 1000u64
            .saturating_mul(1 << self.attempt.min(6))
            .min(MAX_BACKOFF.as_millis() as u64);
        self.attempt = self.attempt.saturating_add(1);
        // xorshift64: jitter only, not security.
        self.seed ^= self.seed << 13;
        self.seed ^= self.seed >> 7;
        self.seed ^= self.seed << 17;
        let jittered = ceiling_ms / 2 + self.seed % (ceiling_ms / 2 + 1);
        let floor_ms = retry_after.unwrap_or(0).min(86_400) * 1000;
        Duration::from_millis(jittered.max(floor_ms))
    }
}

#[cfg(test)]
mod tests {
    use super::Backoff;
    use std::time::Duration;

    #[test]
    fn backoff_doubles_to_sixty_seconds_with_jitter_and_honours_retry_after() {
        let mut backoff = Backoff::new();
        let first = backoff.next(None);
        assert!(first >= Duration::from_millis(500) && first <= Duration::from_secs(1));
        for _ in 0..10 {
            assert!(backoff.next(None) <= Duration::from_secs(60));
        }
        assert!(backoff.next(None) >= Duration::from_secs(30));
        assert_eq!(backoff.next(Some(120)), Duration::from_secs(120));
        backoff.reset();
        assert!(backoff.next(None) <= Duration::from_secs(1));
    }
}
