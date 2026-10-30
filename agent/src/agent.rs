//! The agent loop: enroll, hello, poll for desired state, verify, hold, apply, report, stats.

use std::collections::BTreeSet;
use std::path::PathBuf;
use std::time::{Duration, Instant};

use serde::de::DeserializeOwned;
use serde_json::Value;
use ureq::tls::RootCerts;

use crate::apply::Engine;
use crate::bootstrap::Bootstrap;
use crate::commands::CommandState;
use crate::fetch::{Client, HttpError, load_roots};
use crate::identity::Identity;
use crate::model::{
    NodeStateRef, ObservedStateReport, PROTOCOL, PollRequest, PollResponse, ReportAck, Resource,
    ResourceObservation, Spec, StatsAck,
};
use crate::session::{self, Session};
use crate::state::State;
use crate::stats::{self, CollectionHealth, TrafficStore};
use crate::system::Exec;
use crate::timeutil::{now_rfc3339, now_unix};
use crate::verify::{Admission, Trust, admit, verify_desired};
use crate::{debug, info, report, warn};

mod commands;

/// Report and stats cadence before the first hello names the service's.
pub const REPORT_INTERVAL: Duration = Duration::from_secs(60);
/// Drift re-check and retry of failed resources between new documents.
pub const RECONCILE_INTERVAL: Duration = Duration::from_secs(300);
pub use crate::backoff::Backoff;
use crate::backoff::MAX_BACKOFF;
/// After `unsupported_protocol`, say hello again no sooner than this (or the service's
/// `retry_after_seconds`, if longer): only a matching agent or service can fix it.
pub const PROTOCOL_REFUSED_BACKOFF: Duration = Duration::from_secs(300);
/// After a failed renewal, try again this much later (the certificate outlives several tries).
const RENEW_RETRY_SECONDS: u64 = 3600;

pub struct Options {
    pub bootstrap: PathBuf,
    pub state_dir: PathBuf,
    pub root: PathBuf,
    /// Plain-HTTP service and sources, for tests only.
    pub allow_http: bool,
    pub settle: Duration,
    /// Longest poll the agent asks for; the session's `max_wait_seconds` caps it.
    pub wait_seconds: u64,
    /// A private CA trusted instead of the WebPKI roots (`NETAGENT_TLS_CA_FILE`).
    pub tls_ca_file: Option<PathBuf>,
}

impl Options {
    pub fn production() -> Options {
        Options {
            bootstrap: PathBuf::from("/etc/netagent/bootstrap.json"),
            state_dir: PathBuf::from("/var/lib/netagent"),
            root: PathBuf::from("/"),
            allow_http: false,
            settle: Duration::from_secs(2),
            wait_seconds: 55,
            tls_ca_file: None,
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
    identity: Option<Identity>,
    /// The service certificate's key, once credentials exist; nothing applies without it.
    trust: Option<Trust>,
    session: Option<Session>,
    /// The service revoked this node's certificate: only a fresh enrollment token helps.
    revoked: bool,
    /// The service refused this agent's protocol; no hello is sent before this instant.
    protocol_refused_until: Option<Instant>,
    traffic: TrafficStore,
    collection_health: CollectionHealth,
    /// An Xray API has been installed since start; staleness counts from then.
    collecting: bool,
    roots: Option<RootCerts>,
    exec: &'a dyn Exec,
    opts: Options,
    resources: Vec<Resource>,
    observations: Vec<ResourceObservation>,
    command_state: CommandState,
}

fn typed<T: DeserializeOwned>(body: Value, expected: &str) -> Result<T, HttpError> {
    let kind = body.get("type").and_then(Value::as_str).unwrap_or_default();
    if kind != expected {
        return Err(HttpError::Protocol(format!(
            "expected {expected}, got {kind:?}"
        )));
    }
    serde_json::from_value(body).map_err(|err| HttpError::Protocol(err.to_string()))
}

/// How long to stay quiet after `unsupported_protocol`: the service's hint, never under the floor.
fn refused_wait(err: &HttpError) -> Duration {
    let hinted = match err {
        HttpError::Status { retry_after, .. } => retry_after.unwrap_or(0).min(86_400),
        _ => 0,
    };
    PROTOCOL_REFUSED_BACKOFF.max(Duration::from_secs(hinted))
}

fn protocol_refused_error(left: Duration) -> HttpError {
    HttpError::Status {
        status: 400,
        retry_after: Some(left.as_secs().max(1)),
        code: Some("unsupported_protocol".into()),
    }
}

fn revoked_error() -> HttpError {
    HttpError::Status {
        status: 403,
        retry_after: None,
        code: Some("credential_revoked".into()),
    }
}

impl<'a> Agent<'a> {
    pub fn new(opts: Options, exec: &'a dyn Exec) -> Result<Agent<'a>, String> {
        let bootstrap = Bootstrap::load(&opts.bootstrap, opts.allow_http)?;
        let state = State::load(&opts.state_dir)?;
        let identity = Identity::load(
            &opts.state_dir,
            &bootstrap.ca_sha256,
            &bootstrap.fleet_id,
            &bootstrap.node_id,
            now_unix(),
        )?;
        let traffic = TrafficStore::load(&opts.state_dir)?;
        let command_state = CommandState::load(&opts.state_dir)?;
        let roots = opts.tls_ca_file.as_deref().map(load_roots).transpose()?;
        let client = Client::new(&bootstrap.service_url, opts.allow_http, roots.clone());
        let mut agent = Agent {
            bootstrap,
            state,
            client,
            identity: None,
            trust: None,
            session: None,
            revoked: false,
            protocol_refused_until: None,
            traffic,
            collection_health: CollectionHealth::new(exec.now()),
            collecting: false,
            roots,
            exec,
            opts,
            resources: Vec::new(),
            observations: Vec::new(),
            command_state,
        };
        if let Some(identity) = identity {
            agent.adopt(identity);
        }
        Ok(agent)
    }

    pub fn has_held(&self) -> bool {
        self.state.held.is_some()
    }

    /// Whether the service refused this agent's protocol and no hello is due yet.
    pub fn protocol_refused(&self) -> bool {
        self.protocol_refused_until
            .is_some_and(|until| Instant::now() < until)
    }

    pub fn is_enrolled(&self) -> bool {
        self.identity.is_some() && !self.revoked
    }

    pub fn session(&self) -> Option<&Session> {
        self.session.as_ref()
    }

    pub fn traffic(&self) -> &TrafficStore {
        &self.traffic
    }

    pub fn observations(&self) -> &[ResourceObservation] {
        &self.observations
    }

    /// Use these credentials: trust their service certificate and re-check the held document.
    fn adopt(&mut self, identity: Identity) {
        self.trust = Some(identity.trust(&self.bootstrap.fleet_id, &self.bootstrap.node_id));
        self.identity = Some(identity);
        self.reload_held();
    }

    /// A held document that no longer verifies (a new service certificate, or another node's
    /// state) is dropped; the epoch floor and the ledger survive, so the next document still
    /// retires what this one installed.
    fn reload_held(&mut self) {
        let (Some(held), Some(trust)) = (self.state.held.as_ref(), self.trust.as_ref()) else {
            self.resources = Vec::new();
            return;
        };
        match verify_desired(held, trust) {
            Ok(verified) => self.resources = verified.resources,
            Err(err) => {
                info!("dropping held desired state: {err}");
                self.state.held = None;
                self.state.applied = None;
                self.resources = Vec::new();
                if let Err(err) = self.state.save(&self.opts.state_dir) {
                    info!("{err}");
                }
            }
        }
    }

    // ---- enrollment and sessions --------------------------------------------------------

    /// Make sure there is an open session: enroll if the bootstrap holds a token, say hello
    /// when the session is missing or about to expire, and renew the certificate when due.
    pub fn connect(&mut self) -> Result<(), HttpError> {
        let now = now_unix();
        if let Some(session) = &self.session
            && session.fresh(now)
            && self.client.has_session()
        {
            if now >= session.renew_after {
                self.renew_certificate(now);
            }
            return Ok(());
        }
        self.forget_session();
        if !self.is_enrolled() {
            self.reload_bootstrap();
        }
        if self.bootstrap.enrollment_token.is_some() {
            self.enroll(now)?;
        }
        let Some(identity) = self.identity.as_ref() else {
            return Err(HttpError::Protocol(
                "not enrolled and the bootstrap holds no enrollment token; run `meridian apply`"
                    .into(),
            ));
        };
        if self.revoked {
            return Err(revoked_error());
        }
        if let Some(until) = self.protocol_refused_until {
            let left = until.saturating_duration_since(Instant::now());
            if !left.is_zero() {
                return Err(protocol_refused_error(left));
            }
            self.protocol_refused_until = None;
        }
        let opened = session::hello(
            &self.client,
            &self.bootstrap,
            identity,
            self.state.highest_epoch,
            self.state.applied.as_ref(),
            now,
        );
        let (session, service) = match opened {
            Ok(opened) => opened,
            Err(err) => {
                // An expired or revoked node certificate needs a new token over SSH.
                if matches!(
                    err.code(),
                    Some("credential_revoked" | "credential_expired")
                ) {
                    info!("the service refused this node's certificate; run `meridian apply`");
                    self.revoked = true;
                }
                if err.code() == Some("unsupported_protocol") {
                    let wait = refused_wait(&err);
                    info!(
                        "the control service does not speak {PROTOCOL}; run `meridian apply` to install a \
                         matching agent (next hello in {}s)",
                        wait.as_secs()
                    );
                    self.protocol_refused_until = Some(Instant::now() + wait);
                }
                return Err(err);
            }
        };
        if service.key_id != identity.service.key_id {
            info!("the service presented a new signing certificate");
            let mut updated = identity.clone();
            updated.service = service;
            updated
                .save(&self.opts.state_dir)
                .map_err(HttpError::Protocol)?;
            self.adopt(updated);
        }
        debug!("session open until {}", session.expires_at);
        let due = now >= session.renew_after;
        self.session = Some(session);
        if due {
            self.renew_certificate(now);
        }
        Ok(())
    }

    fn forget_session(&mut self) {
        self.session = None;
        self.client.set_session(None);
    }

    /// The deployer rewrites the bootstrap to enroll the node again; pick it up without a restart.
    fn reload_bootstrap(&mut self) {
        let fresh = match Bootstrap::load(&self.opts.bootstrap, self.opts.allow_http) {
            Ok(fresh) if fresh != self.bootstrap => fresh,
            Ok(_) => return,
            Err(err) => {
                debug!("{err}");
                return;
            }
        };
        if fresh.service_url != self.bootstrap.service_url {
            self.client = Client::new(&fresh.service_url, self.opts.allow_http, self.roots.clone());
        }
        let identity = Identity::load(
            &self.opts.state_dir,
            &fresh.ca_sha256,
            &fresh.fleet_id,
            &fresh.node_id,
            now_unix(),
        );
        self.bootstrap = fresh;
        self.identity = None;
        self.trust = None;
        match identity {
            Ok(Some(identity)) => self.adopt(identity),
            Ok(None) => self.reload_held(),
            Err(err) => info!("{err}"),
        }
    }

    fn enroll(&mut self, now: u64) -> Result<(), HttpError> {
        match session::enroll(&self.client, &self.bootstrap, now) {
            Ok(identity) => {
                identity
                    .save(&self.opts.state_dir)
                    .map_err(HttpError::Protocol)?;
                info!(
                    "enrolled node {} under CA {}",
                    self.bootstrap.node_id,
                    &identity.ca.sha256[..12]
                );
                self.revoked = false;
                self.adopt(identity);
            }
            // Spent by this agent before it could null the token (a crash in between).
            Err(err) if err.code() == Some("enrollment_token_invalid") && self.is_enrolled() => {
                info!("the enrollment token is spent; using the stored credentials");
            }
            Err(err) => return Err(err),
        }
        if let Err(err) = self.bootstrap.spend_token(&self.opts.bootstrap) {
            info!("{err}");
        }
        Ok(())
    }

    /// Certify a fresh key; a failure only postpones the next try (the certificate is still good).
    fn renew_certificate(&mut self, now: u64) {
        match self.renew_now() {
            Ok(()) => info!("renewed the node certificate"),
            Err(err) => {
                info!("certificate renewal failed: {err}");
                if let Some(session) = &mut self.session {
                    session.renew_after = now + RENEW_RETRY_SECONDS;
                }
            }
        }
    }

    /// Run `call` on an open session; a session the service no longer knows is reopened once.
    fn with_session<T>(
        &mut self,
        call: impl Fn(&Client, &Session) -> Result<T, HttpError>,
    ) -> Result<T, HttpError> {
        self.connect()?;
        let first = call(&self.client, self.session.as_ref().expect("connected"));
        match first {
            Err(HttpError::Status { status: 401, .. }) => {
                self.forget_session();
                self.connect()?;
                call(&self.client, self.session.as_ref().expect("connected"))
            }
            Err(err) if err.code() == Some("credential_revoked") => {
                info!("the service revoked this node's certificate; run `meridian apply`");
                self.revoked = true;
                self.forget_session();
                Err(err)
            }
            other => other,
        }
    }

    // ---- apply and report ---------------------------------------------------------------

    /// Apply the held document once (idempotent: unchanged resources are only re-checked).
    /// Without trusted credentials nothing applies: an unverified document never touches the host.
    pub fn apply_held(&mut self) -> Result<(), String> {
        if self.trust.is_none() {
            return Ok(());
        }
        let Some(target) = self.state.held_ref() else {
            return Ok(());
        };
        if target.fleet_epoch < self.state.highest_epoch.unwrap_or(1) {
            return Err("The held document is from an older fleet epoch.".into());
        }
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
                "revision {}/{}: {failed} resource(s) not applied, {} not retired",
                target.fleet_epoch,
                target.revision,
                pass.unretired.len()
            );
        }
        self.state.save(&self.opts.state_dir)
    }

    pub fn build_report(&self) -> ObservedStateReport {
        let collection: Vec<_> = crate::xray_health::wanted(&self.resources)
            .then(|| self.collection_health.check(self.exec.now()))
            .into_iter()
            .collect();
        ObservedStateReport {
            kind: "report",
            fleet_id: self.bootstrap.fleet_id.clone(),
            node_id: self.bootstrap.node_id.clone(),
            applied: self.state.applied.clone(),
            target: self.state.held_ref(),
            resources: self.observations.clone(),
            health: report::health_with_checks(
                &self.opts.root,
                &self.opts.state_dir,
                self.exec,
                &self.resources,
                &self.observations,
                &collection,
            ),
            versions: report::versions(&self.opts.root, &self.resources, &self.observations),
            observed_at: now_rfc3339(),
        }
    }

    pub fn send_report(&mut self) -> Result<(), HttpError> {
        let report = self.build_report();
        let body = self.with_session(|client, _| client.call("report", &report))?;
        typed::<ReportAck>(body, "report_ack").map(|_| ())
    }

    // ---- traffic ------------------------------------------------------------------------

    /// The installed Xray binary and its API address, once the held document names both.
    fn xray_api(&self) -> Option<(String, String)> {
        crate::xray_health::api(&self.opts.root, &self.resources)
            .map(|(binary, address)| (binary, address.to_string()))
    }

    fn managed_users(&self) -> BTreeSet<String> {
        let mut users = BTreeSet::new();
        for resource in &self.resources {
            match &resource.spec {
                Spec::XrayUsers(spec) => users.extend(spec.users.iter().map(|u| u.user_id.clone())),
                Spec::Hysteria2Users(spec) => {
                    users.extend(spec.users.iter().map(|u| u.user_id.clone()))
                }
                _ => {}
            }
        }
        users
    }

    /// Fold Xray's counters into the store and record collection health; `None` while no Xray
    /// API is installed. Staleness counts from the first time one is: a node that waited minutes
    /// for its first document must not report a failed collection it never attempted.
    fn collect_stats(&mut self) -> Option<(String, String)> {
        let api = self.xray_api();
        let (xray, address) = api.as_ref()?;
        if !self.collecting {
            self.collecting = true;
            self.collection_health = CollectionHealth::new(self.exec.now());
        }
        match stats::query(self.exec, xray, address) {
            Ok(deltas) => {
                self.collection_health.success(self.exec.now(), now_unix());
                if !deltas.is_empty() {
                    self.traffic.fold(&deltas);
                    if let Err(err) = self.traffic.save(&self.opts.state_dir) {
                        info!("{err}");
                    }
                }
            }
            // Stored totals still go out: the service counts only growth.
            Err(_) => {
                let now = self.exec.now();
                if self.collection_health.failure(now) {
                    warn!("stats: {}", self.collection_health.check(now).message);
                }
            }
        }
        api
    }

    /// Fold Xray's counters into the store, then send the totals. `Ok(false)` when the session
    /// does not use `traffic_stats` or no Xray runs here yet. Folding comes first, service or
    /// not: Xray's own counters die with the core, so an outage must not leave them there.
    pub fn send_stats(&mut self) -> Result<bool, HttpError> {
        let api = self.collect_stats();
        self.connect()?;
        let uses = self
            .session
            .as_ref()
            .is_some_and(|s| s.uses("traffic_stats"));
        if api.is_none() || !uses {
            return Ok(false);
        }
        let report = self.traffic.next_report(now_rfc3339());
        self.traffic
            .save(&self.opts.state_dir)
            .map_err(HttpError::Protocol)?;
        let body = self.with_session(|client, _| client.call("stats", &report))?;
        let ack: StatsAck = typed(body, "stats_ack")?;
        if self.traffic.acknowledged(&ack, &self.managed_users())
            && let Err(err) = self.traffic.save(&self.opts.state_dir)
        {
            info!("{err}");
        }
        Ok(true)
    }

    // ---- desired state ------------------------------------------------------------------

    /// One long-poll; a new document is verified, held, applied, and reported.
    pub fn poll_once(&mut self) -> Result<PollOutcome, HttpError> {
        self.retry_command_results()?;
        let have = self.state.held_ref();
        let wanted = self.opts.wait_seconds;
        let body = self.with_session(|client, session| {
            client.poll(&PollRequest {
                kind: "poll",
                have: have.clone(),
                wait_seconds: wanted.min(session.max_wait_seconds),
            })
        })?;
        let response: PollResponse = typed(body, "poll_result")?;
        let after = Duration::from_secs(response.poll_after_seconds.min(3600));
        let outcome = match response
            .desired
            .map(|desired| self.accept(desired))
            .unwrap_or(Accepted::Nothing)
        {
            Accepted::Refused => PollOutcome::Refused,
            Accepted::Nothing => PollOutcome::Idle(after),
            Accepted::Recorded => {
                self.report_logged();
                PollOutcome::Recorded(after)
            }
            Accepted::Applied => {
                // The report right after an apply carries collection health: collect first.
                // A merely recorded revision changed nothing, so it resets no counters.
                self.collect_stats();
                self.report_logged();
                PollOutcome::Applied(after)
            }
        };
        self.process_commands(&response.commands, response.fleet_epoch)?;
        Ok(outcome)
    }

    fn report_logged(&mut self) {
        if let Err(err) = self.send_report() {
            info!("report failed: {err}");
        }
    }

    fn accept(&mut self, desired: Value) -> Accepted {
        let Some(trust) = self.trust.as_ref() else {
            return Accepted::Refused;
        };
        let verified = match verify_desired(&desired, trust) {
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
                let mut next = self.state.clone();
                if next.applied.as_ref() == Some(&previous) {
                    next.applied = Some(reference.clone());
                }
                if !self.hold(next, desired, &reference) {
                    return Accepted::Refused;
                }
                self.resources = verified.resources;
                Accepted::Recorded
            }
            Admission::Apply => {
                info!(
                    "received revision {}/{}",
                    reference.fleet_epoch, reference.revision
                );
                if !self.hold(self.state.clone(), desired, &reference) {
                    return Accepted::Refused;
                }
                self.resources = verified.resources;
                if let Err(err) = self.apply_held() {
                    info!("{err}");
                }
                Accepted::Applied
            }
        }
    }

    /// Hold a new document only once `state.json` says so: applying one that was never saved
    /// would let a restart reconcile the older cached document and undo it (revoked users back).
    fn hold(&mut self, mut next: State, desired: Value, reference: &NodeStateRef) -> bool {
        next.held = Some(desired);
        next.highest_epoch = Some(next.highest_epoch.unwrap_or(0).max(reference.fleet_epoch));
        if let Err(err) = next.save(&self.opts.state_dir) {
            info!("not holding revision {}: {err}", reference.revision);
            return false;
        }
        self.state = next;
        true
    }

    fn intervals(&self) -> (Duration, Duration) {
        self.session
            .as_ref()
            .map_or((REPORT_INTERVAL, REPORT_INTERVAL), |s| {
                (s.report_interval, s.stats_interval)
            })
    }

    /// Run forever.
    pub fn run(&mut self) -> ! {
        let mut backoff = Backoff::new();
        if let Err(err) = self.connect() {
            info!("cannot reach the control service yet: {err}");
        }
        if self.has_held()
            && let Err(err) = self.apply_held()
        {
            info!("{err}");
        }
        let mut next_report = Instant::now();
        let mut next_stats = Instant::now();
        let mut next_reconcile = Instant::now() + RECONCILE_INTERVAL;
        loop {
            if Instant::now() >= next_reconcile {
                if let Err(err) = self.apply_held() {
                    info!("{err}");
                }
                next_reconcile = Instant::now() + RECONCILE_INTERVAL;
                next_report = Instant::now();
            }
            // Stats first: the report carries their collection health, which is a warning before any.
            if Instant::now() >= next_stats {
                let result = self.send_stats();
                next_stats = Instant::now()
                    + match result {
                        Ok(_) => self.intervals().1,
                        Err(err) => {
                            debug!("stats failed: {err}");
                            Duration::from_secs(15)
                        }
                    };
            }
            if Instant::now() >= next_report {
                let result = self.send_report();
                next_report = Instant::now()
                    + match result {
                        Ok(()) => self.intervals().0,
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
                    next_report = Instant::now() + self.intervals().0;
                    after
                }
                Ok(PollOutcome::Refused) => backoff.next(None),
                Err(err) if !self.is_enrolled() => {
                    info!("{err}; waiting for a new enrollment token");
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
