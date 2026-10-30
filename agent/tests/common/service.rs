//! A fake control service speaking the agent endpoints: enrollment with the test CA,
//! signed hellos (chain, proof, nonce), sessions, polls, reports, stats, and blobs.

use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::{Arc, Mutex};

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD;
use ed25519_dalek::{Signature, VerifyingKey};
use meridian_agent::identity::proof_signed_bytes;
use meridian_agent::timeutil::{format_unix, now_unix};
use serde_json::{Value, json};
use x509_cert::Certificate;
use x509_cert::der::DecodePem;

use super::pki::TestPki;
use super::{ENROLL_TOKEN, FLEET_ID, MockServer, NODE_ID, Recorded};

pub const PREFIX: &str = "/s3cr3t/agent/v1";

pub struct ServiceState {
    pub pki: TestPki,
    /// Polls answer these documents in turn, then `desired: null`.
    pub documents: VecDeque<Value>,
    /// Deliberately raw commands, including hostile ones; kept until a result is acknowledged.
    pub commands: Vec<Value>,
    pub results: HashMap<String, Value>,
    pub result_refusals: usize,
    pub clock_offset: i64,
    pub reject_results: bool,
    pub failures: HashMap<&'static str, VecDeque<(u16, &'static str)>>,
    pub expire_after_poll: bool,
    pub token_valid: bool,
    pub sessions: HashSet<String>,
    pub revoked: bool,
    /// Seconds after now that hello's `renew_after` names.
    pub renew_in: i64,
    pub capabilities: Vec<&'static str>,
    pub blobs: HashMap<String, Vec<u8>>,
    /// Answer every hello `unsupported_protocol`, as a service of another protocol would.
    pub refuse_protocol: bool,
    nonces: HashSet<String>,
    issued: usize,
}

pub struct FakeService {
    pub server: MockServer,
    pub state: Arc<Mutex<ServiceState>>,
}

fn error(status: u16, code: &str) -> (u16, Vec<u8>) {
    let body = json!({
        "type": "error", "protocol": "meridian.agent/v1",
        "error": {"code": code, "message": code, "retryable": false,
                  "retry_after_seconds": null, "server_time": format_unix(now_unix())},
    });
    (status, serde_json::to_vec(&body).unwrap())
}

fn ok(body: Value) -> (u16, Vec<u8>) {
    (200, serde_json::to_vec(&body).unwrap())
}

fn certificate_key(pem: &str) -> Option<VerifyingKey> {
    let certificate = Certificate::from_pem(pem.as_bytes()).ok()?;
    let raw: [u8; 32] = certificate
        .tbs_certificate
        .subject_public_key_info
        .subject_public_key
        .as_bytes()?
        .try_into()
        .ok()?;
    VerifyingKey::from_bytes(&raw).ok()
}

impl ServiceState {
    fn issue(&mut self, csr: &str) -> Result<String, String> {
        self.issued += 1;
        self.pki.issue_node(NODE_ID, csr)
    }

    fn enroll(&mut self, body: &Value) -> (u16, Vec<u8>) {
        if !self.token_valid || body["enrollment_token"] != ENROLL_TOKEN {
            return error(401, "enrollment_token_invalid");
        }
        let Ok(certificate) = self.issue(body["csr_pem"].as_str().unwrap()) else {
            return error(400, "invalid_request");
        };
        self.token_valid = false;
        self.revoked = false;
        self.sessions.clear();
        ok(json!({
            "type": "enrolled", "protocol": "meridian.agent/v1", "fleet_id": FLEET_ID,
            "node_id": NODE_ID, "fleet_epoch": 1, "certificate_pem": certificate,
            "certificate_expires_at": "2027-01-27T00:00:00Z",
            "ca_certificate_pem": self.pki.ca_pem, "service_certificate_pem": self.pki.service_pem,
        }))
    }

    fn hello(&mut self, body: &Value) -> (u16, Vec<u8>) {
        if self.revoked {
            return error(403, "credential_revoked");
        }
        if self.refuse_protocol {
            let body = json!({
                "type": "error", "protocol": "meridian.agent/v1",
                "error": {"code": "unsupported_protocol", "message": "unsupported_protocol",
                          "retryable": false, "retry_after_seconds": 600,
                          "server_time": format_unix(now_unix())},
            });
            return (400, serde_json::to_vec(&body).unwrap());
        }
        let proof = &body["proof"];
        let key = certificate_key(proof["certificate_pem"].as_str().unwrap()).unwrap();
        let signature = STANDARD
            .decode(proof["signature"].as_str().unwrap())
            .unwrap();
        let signature = Signature::from_slice(&signature).unwrap();
        if key
            .verify_strict(&proof_signed_bytes(body).unwrap(), &signature)
            .is_err()
        {
            return error(401, "unauthenticated");
        }
        if !self
            .nonces
            .insert(proof["nonce"].as_str().unwrap().to_string())
        {
            return error(401, "unauthenticated");
        }
        // Every accepted hello added one nonce, so the count numbers the sessions.
        let token = format!(
            "session-{:04}-0123456789abcdefghijklmnop",
            self.nonces.len()
        );
        self.sessions.insert(token.clone());
        let now = now_unix().saturating_add_signed(self.clock_offset);
        ok(json!({
            "type": "welcome", "protocol": "meridian.agent/v1", "fleet_epoch": 1,
            "capabilities": self.capabilities.iter().filter(|cap| body["capabilities"].as_array().unwrap()
                .iter().any(|offered| offered.as_str() == Some(**cap))).collect::<Vec<_>>(), "session_token": token,
            "session_expires_at": format_unix(now + 3600), "server_time": format_unix(now),
            "poll": {"max_wait_seconds": 55, "report_interval_seconds": 60, "stats_interval_seconds": 60},
            "renew_after": format_unix((now as i64 + self.renew_in) as u64),
            "service_certificate_pem": self.pki.service_pem,
        }))
    }

    fn session(&self, request: &Recorded) -> Result<(), (u16, Vec<u8>)> {
        let token = request
            .authorization
            .as_deref()
            .and_then(|value| value.strip_prefix("Bearer "));
        match token {
            _ if self.revoked => Err(error(403, "credential_revoked")),
            Some(token) if self.sessions.contains(token) => Ok(()),
            _ => Err(error(401, "unauthenticated")),
        }
    }

    fn handle(&mut self, request: &Recorded) -> (u16, Vec<u8>) {
        let Some(endpoint) = request.path.strip_prefix(PREFIX) else {
            return error(404, "not_found");
        };
        if let Some((status, code)) = self
            .failures
            .get_mut(endpoint)
            .and_then(VecDeque::pop_front)
        {
            return error(status, code);
        }
        let body = || request.json();
        match endpoint {
            "/enroll" => return self.enroll(&body()),
            "/hello" => return self.hello(&body()),
            _ => {}
        }
        if let Err(refusal) = self.session(request) {
            return refusal;
        }
        match endpoint {
            "/renew" => match self.issue(body()["csr_pem"].as_str().unwrap()) {
                Ok(certificate) => ok(json!({
                    "type": "renewed", "certificate_pem": certificate,
                    "certificate_expires_at": "2027-01-27T00:00:00Z",
                })),
                Err(_) => error(400, "invalid_request"),
            },
            "/poll" => {
                let desired = self.documents.pop_front().unwrap_or(Value::Null);
                if self.expire_after_poll {
                    self.expire_after_poll = false;
                    self.sessions.clear();
                }
                ok(json!({
                    "type": "poll_result", "fleet_epoch": 1, "desired": desired,
                    "commands": self.commands, "poll_after_seconds": 0,
                }))
            }
            "/command-results" => {
                if self.reject_results {
                    return error(400, "invalid_request");
                }
                let body = body();
                let id = body["command_id"].as_str().unwrap().to_string();
                self.results.entry(id.clone()).or_insert(body);
                self.commands.retain(|command| command["command_id"] != id);
                if self.result_refusals > 0 {
                    self.result_refusals -= 1;
                    return error(503, "temporarily_unavailable");
                }
                ok(json!({"type": "command_result_ack", "command_id": id}))
            }
            "/report" => ok(json!({"type": "report_ack", "applied_revision": null})),
            "/stats" => {
                let body = body();
                ok(json!({
                    "type": "stats_ack", "counter_epoch": body["counter_epoch"],
                    "acked_seq": body["seq"],
                }))
            }
            blob => match blob
                .strip_prefix("/blobs/")
                .and_then(|sha| self.blobs.get(sha))
            {
                Some(bytes) => (200, bytes.clone()),
                None => error(404, "not_found"),
            },
        }
    }
}

impl FakeService {
    pub fn start(pki: &TestPki) -> FakeService {
        let state = Arc::new(Mutex::new(ServiceState {
            pki: pki.clone(),
            documents: VecDeque::new(),
            commands: Vec::new(),
            results: HashMap::new(),
            result_refusals: 0,
            clock_offset: 0,
            reject_results: false,
            failures: HashMap::new(),
            expire_after_poll: false,
            token_valid: true,
            sessions: HashSet::new(),
            revoked: false,
            renew_in: 86_400,
            capabilities: vec!["long_poll", "xray_config", "xray_users", "traffic_stats"],
            blobs: HashMap::new(),
            refuse_protocol: false,
            nonces: HashSet::new(),
            issued: 0,
        }));
        let shared = Arc::clone(&state);
        let server = MockServer::start(Box::new(move |request| {
            shared.lock().unwrap().handle(request)
        }));
        FakeService { server, state }
    }

    /// The bootstrap's `service_url` (the secret path included).
    pub fn url(&self) -> String {
        format!("{}/s3cr3t", self.server.url)
    }

    pub fn push(&self, document: Value) {
        self.state.lock().unwrap().documents.push_back(document);
    }

    /// Requested endpoint names in order (`poll`, `hello`, `blobs/…`).
    pub fn endpoints(&self) -> Vec<String> {
        self.server
            .requests()
            .iter()
            .map(|r| {
                r.path
                    .trim_start_matches(PREFIX)
                    .trim_start_matches('/')
                    .to_string()
            })
            .collect()
    }

    pub fn requests_to(&self, endpoint: &str) -> Vec<Recorded> {
        self.server
            .requests()
            .into_iter()
            .filter(|r| r.path == format!("{PREFIX}/{endpoint}"))
            .collect()
    }
}
