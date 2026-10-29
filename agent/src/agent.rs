//! The agent loop: poll for desired state, verify, hold, apply, report.

use std::path::PathBuf;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::Value;

use crate::apply::Engine;
use crate::bootstrap::Bootstrap;
use crate::fetch::{Client, HttpError};
use crate::model::{
    NodeStateRef, ObservedStateReport, PollRequest, PollResponse, ReportAck, Resource,
    ResourceObservation,
};
use crate::state::State;
use crate::system::Exec;
use crate::timeutil::now_rfc3339;
use crate::verify::{Admission, admit, verify_desired};
use crate::{debug, info, report};

pub const REPORT_INTERVAL: Duration = Duration::from_secs(60);
/// Drift re-check and retry of failed resources between new documents.
pub const RECONCILE_INTERVAL: Duration = Duration::from_secs(300);
const MAX_BACKOFF: Duration = Duration::from_secs(60);

pub struct Options {
    pub bootstrap: PathBuf,
    pub state_dir: PathBuf,
    pub root: PathBuf,
    /// Plain-HTTP service and sources, for tests only.
    pub allow_http: bool,
    pub settle: Duration,
    pub wait_seconds: u64,
}

impl Options {
    pub fn production() -> Options {
        Options {
            bootstrap: PathBuf::from("/etc/netagent/bootstrap.json"),
            state_dir: PathBuf::from("/var/lib/netagent"),
            root: PathBuf::from("/"),
            allow_http: false,
            settle: Duration::from_secs(2),
            wait_seconds: 25,
        }
    }
}

/// What one poll produced.
#[derive(Debug, PartialEq, Eq)]
pub enum PollOutcome {
    /// Nothing new; poll again after this long.
    Idle(Duration),
    /// A new document was applied (and reported).
    Applied(Duration),
    /// Same content under a newer revision was recorded (and reported).
    Recorded(Duration),
    /// The document was refused; back off before polling again.
    Refused,
}

pub struct Agent<'a> {
    pub bootstrap: Bootstrap,
    pub state: State,
    pub client: Client,
    exec: &'a dyn Exec,
    opts: Options,
    resources: Vec<Resource>,
    observations: Vec<ResourceObservation>,
    commands_logged: bool,
}

impl<'a> Agent<'a> {
    pub fn new(opts: Options, exec: &'a dyn Exec) -> Result<Agent<'a>, String> {
        let bootstrap = Bootstrap::load(&opts.bootstrap, opts.allow_http)?;
        let mut state = State::load(&opts.state_dir)?;
        // A held document that no longer verifies (re-bootstrap with a new service key, or
        // another node's state) is dropped; the epoch floor and the ledger survive, so the
        // next document still retires what this one installed.
        let resources = match state
            .held
            .as_ref()
            .map(|held| verify_desired(held, &bootstrap))
        {
            Some(Ok(verified)) => verified.resources,
            Some(Err(err)) => {
                info!("dropping held desired state: {err}");
                state.held = None;
                state.applied = None;
                Vec::new()
            }
            None => Vec::new(),
        };
        let client = Client::new(&bootstrap, opts.allow_http);
        Ok(Agent {
            bootstrap,
            state,
            client,
            exec,
            opts,
            resources,
            observations: Vec::new(),
            commands_logged: false,
        })
    }

    pub fn has_held(&self) -> bool {
        self.state.held.is_some()
    }

    pub fn observations(&self) -> &[ResourceObservation] {
        &self.observations
    }

    /// Apply the held document once (idempotent: unchanged resources are only re-checked).
    pub fn apply_held(&mut self) -> Result<(), String> {
        let Some(target) = self.state.held_ref() else {
            return Ok(());
        };
        let engine = Engine {
            root: self.opts.root.clone(),
            cache_dir: self.opts.state_dir.join("cache"),
            exec: self.exec,
            fetch: &self.client,
            settle: self.opts.settle,
            allow_http: self.opts.allow_http,
        };
        let pass = engine.apply(
            &self.resources,
            &mut self.state.ledger,
            &self.observations,
            &now_rfc3339(),
        );
        self.observations = pass.observations;
        if pass.converged {
            if self.state.applied.as_ref() != Some(&target) {
                info!(
                    "applied revision {}/{}",
                    target.fleet_epoch, target.revision
                );
            }
            self.state.applied = Some(target);
        } else {
            let failed = self
                .observations
                .iter()
                .filter(|o| o.status != "applied")
                .count();
            info!(
                "revision {}/{}: {failed} resource(s) not applied",
                target.fleet_epoch, target.revision
            );
        }
        self.state.save(&self.opts.state_dir)
    }

    pub fn build_report(&self) -> ObservedStateReport {
        ObservedStateReport {
            kind: "report",
            fleet_id: self.bootstrap.fleet_id.clone(),
            node_id: self.bootstrap.node_id.clone(),
            applied: self.state.applied.clone(),
            target: self.state.held_ref(),
            resources: self.observations.clone(),
            health: report::health(
                &self.opts.root,
                &self.opts.state_dir,
                self.exec,
                &self.resources,
                &self.observations,
            ),
            versions: report::versions(&self.opts.root, &self.resources, &self.observations),
            observed_at: now_rfc3339(),
        }
    }

    pub fn send_report(&self) -> Result<(), HttpError> {
        let body = self.client.report(&self.build_report())?;
        let ack: ReportAck =
            serde_json::from_value(body).map_err(|err| HttpError::Protocol(err.to_string()))?;
        if ack.kind != "report_ack" {
            return Err(HttpError::Protocol(format!(
                "expected report_ack, got {}",
                ack.kind
            )));
        }
        Ok(())
    }

    /// One long-poll; a new document is verified, held, applied, and reported.
    pub fn poll_once(&mut self) -> Result<PollOutcome, HttpError> {
        let request = PollRequest {
            kind: "poll",
            have: self.state.held_ref(),
            wait_seconds: self.opts.wait_seconds,
        };
        let body = self.client.poll(&request)?;
        let response: PollResponse =
            serde_json::from_value(body).map_err(|err| HttpError::Protocol(err.to_string()))?;
        if response.kind != "poll_result" {
            return Err(HttpError::Protocol(format!(
                "expected poll_result, got {}",
                response.kind
            )));
        }
        if !response.commands.is_empty() && !self.commands_logged {
            debug!(
                "ignoring {} command(s): the skeleton runs none",
                response.commands.len()
            );
            self.commands_logged = true;
        }
        let after = Duration::from_secs(response.poll_after_seconds.min(3600));
        let Some(desired) = response.desired else {
            return Ok(PollOutcome::Idle(after));
        };
        Ok(match self.accept(desired) {
            Accepted::Refused => PollOutcome::Refused,
            Accepted::Nothing => PollOutcome::Idle(after),
            Accepted::Recorded => {
                self.report_logged();
                PollOutcome::Recorded(after)
            }
            Accepted::Applied => {
                self.report_logged();
                PollOutcome::Applied(after)
            }
        })
    }

    fn report_logged(&self) {
        if let Err(err) = self.send_report() {
            info!("report failed: {err}");
        }
    }

    fn accept(&mut self, desired: Value) -> Accepted {
        let verified = match verify_desired(&desired, &self.bootstrap) {
            Ok(verified) => verified,
            Err(err) => {
                info!("refused desired state: {err}");
                return Accepted::Refused;
            }
        };
        let reference = verified.reference.clone();
        let held = self.state.held_ref();
        match admit(self.state.highest_epoch, held.as_ref(), &reference) {
            Admission::Refuse(reason) => {
                info!("refused desired state: {reason}");
                Accepted::Refused
            }
            Admission::AlreadyHeld => Accepted::Nothing,
            Admission::RecordRevision => {
                let previous = held.expect("a revision is recorded only over a held document");
                if self.state.applied.as_ref() == Some(&previous) {
                    self.state.applied = Some(reference.clone());
                }
                self.hold(desired, &reference);
                self.resources = verified.resources;
                if let Err(err) = self.state.save(&self.opts.state_dir) {
                    info!("{err}");
                }
                Accepted::Recorded
            }
            Admission::Apply => {
                info!(
                    "received revision {}/{}",
                    reference.fleet_epoch, reference.revision
                );
                self.hold(desired, &reference);
                self.resources = verified.resources;
                if let Err(err) = self.state.save(&self.opts.state_dir) {
                    info!("{err}");
                }
                if let Err(err) = self.apply_held() {
                    info!("{err}");
                }
                Accepted::Applied
            }
        }
    }

    fn hold(&mut self, desired: Value, reference: &NodeStateRef) {
        self.state.held = Some(desired);
        self.state.highest_epoch = Some(
            self.state
                .highest_epoch
                .unwrap_or(0)
                .max(reference.fleet_epoch),
        );
    }

    /// Run forever.
    pub fn run(&mut self) -> ! {
        let mut backoff = Backoff::new();
        if self.has_held()
            && let Err(err) = self.apply_held()
        {
            info!("{err}");
        }
        let mut next_report = Instant::now();
        let mut next_reconcile = Instant::now() + RECONCILE_INTERVAL;
        loop {
            if Instant::now() >= next_reconcile {
                if let Err(err) = self.apply_held() {
                    info!("{err}");
                }
                next_reconcile = Instant::now() + RECONCILE_INTERVAL;
                next_report = Instant::now();
            }
            if Instant::now() >= next_report {
                next_report = Instant::now()
                    + match self.send_report() {
                        Ok(()) => REPORT_INTERVAL,
                        Err(err) => {
                            debug!("report failed: {err}");
                            Duration::from_secs(15)
                        }
                    };
            }
            let pause = match self.poll_once() {
                Ok(PollOutcome::Idle(after)) => {
                    backoff.reset();
                    after
                }
                Ok(PollOutcome::Applied(after)) | Ok(PollOutcome::Recorded(after)) => {
                    backoff.reset();
                    next_report = Instant::now() + REPORT_INTERVAL;
                    after
                }
                Ok(PollOutcome::Refused) => backoff.next(None),
                Err(HttpError::Status {
                    status: 401 | 403, ..
                }) => {
                    info!("node token refused; run `meridian apply` to re-enroll");
                    MAX_BACKOFF
                }
                Err(err) => {
                    let retry_after = match &err {
                        HttpError::Status { retry_after, .. } => *retry_after,
                        _ => None,
                    };
                    let delay = backoff.next(retry_after);
                    info!(
                        "poll failed: {err}; retrying in {:.1}s",
                        delay.as_secs_f64()
                    );
                    delay
                }
            };
            std::thread::sleep(pause);
        }
    }
}

enum Accepted {
    Applied,
    Recorded,
    Nothing,
    Refused,
}

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
