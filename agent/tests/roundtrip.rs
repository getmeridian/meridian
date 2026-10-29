//! enroll → hello → poll → verify → apply → report → stats against an in-process fake service.

mod common;

use std::os::unix::fs::PermissionsExt;
use std::time::Duration;

use common::pki::TestPki;
use common::service::FakeService;
use common::*;
use meridian_agent::agent::{Agent, Options, PollOutcome};
use meridian_agent::fetch::HttpError;
use meridian_agent::jcs;
use meridian_agent::selfcheck::self_check;
use serde_json::{Value, json};

const XRAY: &[u8] = b"\x7fELF fake xray";
const UUID_A: &str = "3f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00";

fn resources() -> Vec<Value> {
    vec![
        pin(
            "pin:xray",
            "xray",
            "service",
            XRAY,
            "none",
            None,
            "/usr/local/lib/netagent/netcore",
        ),
        xray_config(&["pin:xray"], None),
        vless_users(&[("u-a", UUID_A, "xtls-rprx-vision")]),
        unit(
            "unit:netcore",
            "netcore.service",
            &["xray:config", "users:vless"],
            &["xray:config", "users:vless"],
        ),
        json!({"kind": "firewall_rule", "id": "fw:443", "depends_on": [], "port": 443, "protocol": "tcp", "allow_from": []}),
    ]
}

fn supported() -> Vec<Value> {
    resources().into_iter().take(4).collect()
}

fn service(pki: &TestPki) -> FakeService {
    let service = FakeService::start(pki);
    service
        .state
        .lock()
        .unwrap()
        .blobs
        .insert(jcs::sha256_hex(XRAY), XRAY.to_vec());
    service
}

struct Node {
    root: TempDir,
    state: TempDir,
    etc: TempDir,
}

impl Node {
    fn new(service_url: &str, pki: &TestPki) -> Node {
        let node = Node {
            root: host_root(),
            state: TempDir::new("state"),
            etc: TempDir::new("etc"),
        };
        node.write_bootstrap(&bootstrap_json(service_url, pki));
        node
    }

    fn write_bootstrap(&self, boot: &Value) {
        std::fs::write(self.bootstrap(), serde_json::to_vec(boot).unwrap()).unwrap();
    }

    fn bootstrap(&self) -> std::path::PathBuf {
        self.etc.path().join("bootstrap.json")
    }

    fn read_bootstrap(&self) -> Value {
        serde_json::from_slice(&std::fs::read(self.bootstrap()).unwrap()).unwrap()
    }

    fn options(&self) -> Options {
        Options {
            bootstrap: self.bootstrap(),
            state_dir: self.state.path().to_path_buf(),
            root: self.root.path().to_path_buf(),
            allow_http: true,
            settle: Duration::ZERO,
            wait_seconds: 0,
        }
    }
}

fn mode(path: &std::path::Path) -> u32 {
    std::fs::metadata(path).unwrap().permissions().mode() & 0o777
}

#[test]
fn enroll_hello_poll_apply_report_round_trip() {
    let pki = TestPki::new();
    let service = service(&pki);
    service.push(document(1, 1, &resources(), &pki));
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    assert!(!agent.is_enrolled());

    assert_eq!(
        agent.poll_once().unwrap(),
        PollOutcome::Applied(Duration::ZERO)
    );
    assert_eq!(
        agent.poll_once().unwrap(),
        PollOutcome::Idle(Duration::ZERO)
    );

    let endpoints = service.endpoints();
    assert_eq!(&endpoints[..3], ["enroll", "hello", "poll"]);
    assert!(endpoints[3].starts_with("blobs/"));
    assert_eq!(&endpoints[4..], ["report", "poll"]);

    // Enrollment sends a CSR, never a key; the token is then spent on disk.
    let enroll = service.requests_to("enroll")[0].json();
    assert_eq!(enroll["enrollment_token"], ENROLL_TOKEN);
    assert!(
        enroll["csr_pem"]
            .as_str()
            .unwrap()
            .starts_with("-----BEGIN CERTIFICATE REQUEST-----")
    );
    assert!(service.requests_to("enroll")[0].authorization.is_none());
    let boot = node.read_bootstrap();
    assert_eq!(boot["enrollment_token"], Value::Null);
    assert_eq!(boot["token_expires_at"], Value::Null);
    assert_eq!(boot["ca_sha256"], pki.ca_sha256());
    assert_eq!(mode(&node.bootstrap()), 0o600);
    let identity = node.state.path().join("identity.json");
    assert_eq!(mode(&identity), 0o600);

    // Hello carries a signed proof and no bearer; session endpoints carry the session token.
    let hello = &service.requests_to("hello")[0];
    assert!(hello.authorization.is_none());
    let hello = hello.json();
    assert_eq!(hello["capabilities"].as_array().unwrap().len(), 9);
    assert_eq!(hello["highest_fleet_epoch"], Value::Null);
    let poll = &service.requests_to("poll")[0];
    assert!(
        poll.authorization
            .as_deref()
            .unwrap()
            .starts_with("Bearer session-")
    );
    assert_eq!(
        poll.json(),
        json!({"type": "poll", "have": null, "wait_seconds": 0})
    );
    assert_eq!(service.requests_to("poll")[1].json()["have"]["revision"], 1);

    let report = service.requests_to("report")[0].json();
    assert_eq!(report["fleet_id"], FLEET_ID);
    assert_eq!(
        report["applied"],
        Value::Null,
        "the unsupported firewall rule keeps it unapplied"
    );
    assert_eq!(report["target"]["revision"], 1);
    let statuses: Vec<(&str, &str)> = report["resources"]
        .as_array()
        .unwrap()
        .iter()
        .map(|o| (o["id"].as_str().unwrap(), o["status"].as_str().unwrap()))
        .collect();
    assert_eq!(
        statuses,
        [
            ("pin:xray", "applied"),
            ("xray:config", "applied"),
            ("users:vless", "applied"),
            ("unit:netcore", "applied"),
            ("fw:443", "failed")
        ]
    );
    assert_eq!(report["resources"][4]["error"]["code"], "unsupported");
    assert_eq!(report["health"]["status"], "degraded");
    assert_eq!(report["versions"]["xray"], "26.7.11");
    let text = String::from_utf8(service.requests_to("report")[0].body.clone()).unwrap();
    assert!(!text.contains(UUID_A), "reports never carry user UUIDs");

    // A restarted agent keeps its credentials and re-applies the held document offline.
    drop(agent);
    exec.clear();
    let mut restarted = Agent::new(node.options(), &exec).unwrap();
    assert!(restarted.is_enrolled() && restarted.has_held());
    restarted.apply_held().unwrap();
    assert!(
        exec.calls()
            .iter()
            .all(|c| !c.contains("restart") && !c.contains(" start"))
    );
    restarted.poll_once().unwrap();
    assert_eq!(
        service.requests_to("enroll").len(),
        1,
        "never enrolls twice"
    );
    let hellos = service.requests_to("hello");
    assert_eq!(hellos.len(), 2);
    assert_ne!(
        hellos[0].json()["proof"]["nonce"],
        hellos[1].json()["proof"]["nonce"]
    );
    assert_eq!(hellos[1].json()["highest_fleet_epoch"], 1);
}

#[test]
fn a_converged_document_is_recorded_and_same_content_bumps_only_the_revision() {
    let pki = TestPki::new();
    let service = service(&pki);
    for revision in [5, 6, 4] {
        service.push(document(2, revision, &supported(), &pki));
    }
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    assert_eq!(agent.state.applied.as_ref().unwrap().revision, 5);

    exec.clear();
    assert_eq!(
        agent.poll_once().unwrap(),
        PollOutcome::Recorded(Duration::ZERO)
    );
    assert_eq!(agent.state.applied.as_ref().unwrap().revision, 6);
    assert!(
        exec.calls()
            .iter()
            .all(|c| c.starts_with("systemctl is-active")),
        "{:?}",
        exec.calls()
    );
    assert_eq!(agent.poll_once().unwrap(), PollOutcome::Refused);
    assert_eq!(agent.state.held_ref().unwrap().revision, 6);
}

#[test]
fn a_document_signed_by_another_certificate_is_refused_and_nothing_is_held() {
    let pki = TestPki::new();
    let service = service(&pki);
    service.push(document(1, 1, &resources(), &TestPki::with_service_seed(9)));
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    assert_eq!(agent.poll_once().unwrap(), PollOutcome::Refused);
    assert!(!agent.has_held());
    assert!(exec.calls().is_empty());
}

#[test]
fn a_spent_enrollment_token_leaves_the_node_unenrolled() {
    let pki = TestPki::new();
    let service = service(&pki);
    service.state.lock().unwrap().token_valid = false;
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    let err = agent.poll_once().unwrap_err();
    assert_eq!(err.code(), Some("enrollment_token_invalid"));
    assert!(!agent.is_enrolled());
    assert!(!node.state.path().join("identity.json").exists());
}

#[test]
fn a_ca_other_than_the_pinned_one_is_refused_at_enrollment() {
    let pki = TestPki::new();
    let service = service(&pki);
    let node = Node::new(&service.url(), &pki);
    let mut boot = node.read_bootstrap();
    boot["ca_sha256"] = json!("ab".repeat(32));
    node.write_bootstrap(&boot);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    let err = agent.poll_once().unwrap_err();
    assert!(err.to_string().contains("pinned ca_sha256"), "{err}");
    assert!(!node.state.path().join("identity.json").exists());
}

#[test]
fn a_token_spent_before_a_crash_falls_back_to_the_stored_credentials() {
    let pki = TestPki::new();
    let service = service(&pki);
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    drop(agent);

    // The agent died after saving its credentials but before nulling the token.
    node.write_bootstrap(&bootstrap_json(&service.url(), &pki));
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    assert_eq!(
        agent.poll_once().unwrap(),
        PollOutcome::Idle(Duration::ZERO)
    );
    assert_eq!(node.read_bootstrap()["enrollment_token"], Value::Null);
    assert_eq!(service.requests_to("enroll").len(), 2);
}

#[test]
fn a_session_the_service_forgot_is_reopened_with_a_fresh_hello() {
    let pki = TestPki::new();
    let service = service(&pki);
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    service.state.lock().unwrap().sessions.clear();

    agent.poll_once().unwrap();
    assert_eq!(
        service.endpoints(),
        ["enroll", "hello", "poll", "poll", "hello", "poll"]
    );
}

#[test]
fn a_service_that_refuses_the_protocol_is_not_asked_again_until_the_backoff_ends() {
    let pki = TestPki::new();
    let service = service(&pki);
    service.state.lock().unwrap().refuse_protocol = true;
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();

    let err = agent.poll_once().unwrap_err();
    assert_eq!(err.code(), Some("unsupported_protocol"));
    assert!(agent.protocol_refused());
    assert!(
        agent.is_enrolled(),
        "a refused protocol never touches the credentials"
    );

    // Neither polls nor reports say hello again inside the window, which honours the
    // service's longer hint: a refusal is a long backoff, never a crash or a hello storm.
    let again = agent.poll_once().unwrap_err();
    assert!(matches!(again, HttpError::Status { retry_after: Some(wait), .. } if wait > 300));
    assert!(agent.send_report().is_err());
    assert_eq!(service.endpoints(), ["enroll", "hello"]);
}

#[test]
fn a_revoked_node_stops_until_a_new_bootstrap_enrolls_it_again() {
    let pki = TestPki::new();
    let service = service(&pki);
    service.push(document(1, 1, &supported(), &pki));
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    service.state.lock().unwrap().revoked = true;

    let err = agent.poll_once().unwrap_err();
    assert_eq!(err.code(), Some("credential_revoked"));
    assert!(!agent.is_enrolled());
    assert!(matches!(
        agent.poll_once(),
        Err(HttpError::Status { status: 403, .. })
    ));

    // The deployer writes a fresh token; the agent picks it up without a restart.
    service.state.lock().unwrap().token_valid = true;
    node.write_bootstrap(&bootstrap_json(&service.url(), &pki));
    assert_eq!(
        agent.poll_once().unwrap(),
        PollOutcome::Idle(Duration::ZERO)
    );
    assert!(agent.is_enrolled());
    assert!(agent.has_held(), "the held document survives re-enrollment");
    assert_eq!(service.requests_to("enroll").len(), 2);
}

#[test]
fn a_due_certificate_is_renewed_with_a_fresh_key() {
    let pki = TestPki::new();
    let service = service(&pki);
    service.state.lock().unwrap().renew_in = 0;
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    let identity = node.state.path().join("identity.json");
    agent.connect().unwrap();
    let renewed: Value = serde_json::from_slice(&std::fs::read(&identity).unwrap()).unwrap();
    assert_eq!(service.endpoints(), ["enroll", "hello", "renew"]);
    let enroll_csr = service.requests_to("enroll")[0].json()["csr_pem"].clone();
    assert_ne!(
        service.requests_to("renew")[0].json()["csr_pem"],
        enroll_csr
    );

    // The next hello proves possession with the renewed certificate.
    {
        let mut state = service.state.lock().unwrap();
        state.sessions.clear();
        state.renew_in = 86_400;
    }
    agent.poll_once().unwrap();
    let hello = service.requests_to("hello")[1].json();
    assert_eq!(
        hello["proof"]["certificate_pem"],
        renewed["certificate_pem"]
    );
    assert_eq!(service.requests_to("renew").len(), 1);
}

#[test]
fn a_new_service_certificate_drops_the_held_document_but_keeps_ledger_and_floor() {
    let pki = TestPki::new();
    let service = service(&pki);
    service.push(document(4, 1, &resources(), &pki));
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();

    let rotated = TestPki::with_service_seed(9);
    {
        let mut state = service.state.lock().unwrap();
        state.pki = rotated.clone();
        state.sessions.clear();
        state
            .documents
            .push_back(document(4, 2, &supported(), &rotated));
    }
    assert_eq!(
        agent.poll_once().unwrap(),
        PollOutcome::Applied(Duration::ZERO)
    );
    assert_eq!(agent.state.highest_epoch, Some(4));
    assert_eq!(agent.state.held_ref().unwrap().revision, 2);
    assert!(!agent.state.ledger.is_empty());

    // The stored credentials now trust the new certificate.
    drop(agent);
    let agent = Agent::new(node.options(), &exec).unwrap();
    assert!(agent.has_held());
}

#[test]
fn traffic_is_folded_sent_cumulatively_and_removed_users_forgotten_after_ack() {
    let pki = TestPki::new();
    let service = service(&pki);
    service.push(document(1, 1, &supported(), &pki));
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();

    *exec.stats_output.lock().unwrap() = r#"{"stat":[
        {"name":"user>>>u-a>>>traffic>>>uplink","value":"100"},
        {"name":"user>>>u-a>>>traffic>>>downlink","value":"900"},
        {"name":"user>>>u-gone>>>traffic>>>downlink","value":"5"}]}"#
        .into();
    assert!(agent.send_stats().unwrap());
    *exec.stats_output.lock().unwrap() =
        r#"{"stat":[{"name":"user>>>u-a>>>traffic>>>uplink","value":"1"}]}"#.into();
    assert!(agent.send_stats().unwrap());

    let sent = service.requests_to("stats");
    let first = sent[0].json();
    let second = sent[1].json();
    assert_eq!(
        (first["seq"].as_u64(), second["seq"].as_u64()),
        (Some(1), Some(2))
    );
    assert_eq!(first["counter_epoch"], second["counter_epoch"]);
    assert_eq!(
        first["users"],
        json!([
            {"user_id": "u-a", "uplink_bytes": 100, "downlink_bytes": 900},
            {"user_id": "u-gone", "uplink_bytes": 0, "downlink_bytes": 5},
        ])
    );
    // u-gone is not in the document: forgotten once its final total was acknowledged.
    assert_eq!(
        second["users"],
        json!([{"user_id": "u-a", "uplink_bytes": 101, "downlink_bytes": 900}])
    );
    assert_eq!(agent.traffic().users.len(), 1);
    let store = node.state.path().join("traffic.json");
    assert_eq!(mode(&store), 0o600);

    // Counters survive a restart under the same epoch.
    drop(agent);
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    assert!(agent.send_stats().unwrap());
    let third = service.requests_to("stats")[2].json();
    assert_eq!(third["counter_epoch"], first["counter_epoch"]);
    assert_eq!(third["seq"], 3);
    assert_eq!(third["users"][0]["uplink_bytes"], 101);
}

#[test]
fn stats_wait_for_the_capability_and_an_installed_xray() {
    let pki = TestPki::new();
    let service = service(&pki);
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    assert!(!agent.send_stats().unwrap(), "no xray yet");

    service.state.lock().unwrap().capabilities = vec!["long_poll"];
    service.push(document(1, 1, &supported(), &pki));
    service.state.lock().unwrap().sessions.clear();
    agent.poll_once().unwrap();
    assert!(
        !agent.send_stats().unwrap(),
        "the service does not use traffic_stats"
    );
    assert!(service.requests_to("stats").is_empty());
}

#[test]
fn a_malformed_state_file_stops_the_agent() {
    let pki = TestPki::new();
    let node = Node::new("http://127.0.0.1:9/s3cr3t", &pki);
    std::fs::write(node.state.path().join("state.json"), b"{not json").unwrap();
    let exec = FakeExec::default();
    let err = Agent::new(node.options(), &exec).err().unwrap();
    assert!(err.contains("malformed"), "{err}");
}

#[test]
fn malformed_credentials_stop_the_agent() {
    let pki = TestPki::new();
    let node = Node::new("http://127.0.0.1:9/s3cr3t", &pki);
    std::fs::write(node.state.path().join("identity.json"), b"{").unwrap();
    let exec = FakeExec::default();
    let err = Agent::new(node.options(), &exec).err().unwrap();
    assert!(err.contains("malformed"), "{err}");
}

fn files(dir: &std::path::Path) -> Vec<(String, Vec<u8>)> {
    let mut entries: Vec<(String, Vec<u8>)> = std::fs::read_dir(dir)
        .unwrap()
        .filter_map(|entry| {
            let path = entry.unwrap().path();
            path.is_file().then(|| {
                (
                    path.file_name().unwrap().to_string_lossy().into_owned(),
                    std::fs::read(&path).unwrap(),
                )
            })
        })
        .collect();
    entries.sort();
    entries
}

#[test]
fn a_staged_build_checks_the_nodes_files_without_writing_or_dialing() {
    let pki = TestPki::new();
    let service = service(&pki);
    service.push(document(1, 1, &supported(), &pki));
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    let before = (files(node.state.path()), node.read_bootstrap());
    let requests = service.endpoints().len();

    let report = self_check(&node.bootstrap(), node.state.path(), true).unwrap();

    assert_eq!(report["version"], meridian_agent::VERSION);
    assert_eq!(report["node_id"], NODE_ID);
    assert_eq!(report["enrolled"], true);
    assert_eq!(report["held_verifies"], true);
    assert_eq!((files(node.state.path()), node.read_bootstrap()), before);
    assert_eq!(service.endpoints().len(), requests, "check never dials");

    // A build that cannot read the node's files refuses to take over.
    std::fs::write(node.state.path().join("state.json"), b"{not json").unwrap();
    let err = self_check(&node.bootstrap(), node.state.path(), true).unwrap_err();
    assert!(err.contains("malformed"), "{err}");
}
