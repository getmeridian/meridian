//! poll → verify → apply → report against an in-process HTTP service.

mod common;

use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use common::*;
use meridian_agent::agent::{Agent, Options, PollOutcome};
use meridian_agent::fetch::HttpError;
use meridian_agent::jcs;
use serde_json::{Value, json};

const XRAY: &[u8] = b"\x7fELF fake xray";

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
        vless_users(&[(
            "u-a",
            "3f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00",
            "xtls-rprx-vision",
        )]),
        unit(
            "unit:netcore",
            "netcore.service",
            &["xray:config", "users:vless"],
            &["xray:config", "users:vless"],
        ),
        json!({"kind": "firewall_rule", "id": "fw:443", "depends_on": [], "port": 443, "protocol": "tcp", "allow_from": []}),
    ]
}

fn service(desired: Value) -> MockServer {
    let polls = Arc::new(AtomicUsize::new(0));
    let blob_path = format!("/s3cr3t/agent/v1/blobs/{}", jcs::sha256_hex(XRAY));
    MockServer::start(Box::new(move |request| {
        if request.authorization.as_deref() != Some(&format!("Bearer {TOKEN}")) {
            return (401, br#"{"type":"error"}"#.to_vec());
        }
        match (request.method.as_str(), request.path.as_str()) {
            ("POST", "/s3cr3t/agent/v1/poll") => {
                let first = polls.fetch_add(1, Ordering::SeqCst) == 0;
                let body = json!({
                    "type": "poll_result", "fleet_epoch": 1,
                    "desired": if first { desired.clone() } else { Value::Null },
                    "commands": [], "poll_after_seconds": 0,
                });
                (200, serde_json::to_vec(&body).unwrap())
            }
            ("POST", "/s3cr3t/agent/v1/report") => (
                200,
                br#"{"type":"report_ack","applied_revision":null}"#.to_vec(),
            ),
            ("GET", path) if path == blob_path => (200, XRAY.to_vec()),
            _ => (404, br#"{"type":"error"}"#.to_vec()),
        }
    }))
}

struct Node {
    root: TempDir,
    state: TempDir,
    etc: TempDir,
}

impl Node {
    fn new(url: &str, token: &str) -> Node {
        let node = Node {
            root: host_root(),
            state: TempDir::new("state"),
            etc: TempDir::new("etc"),
        };
        let mut boot = bootstrap_json(&format!("{url}/s3cr3t"), &signing_key());
        boot["node_token"] = json!(token);
        std::fs::write(
            node.etc.path().join("bootstrap.json"),
            serde_json::to_vec(&boot).unwrap(),
        )
        .unwrap();
        node
    }

    fn options(&self) -> Options {
        Options {
            bootstrap: self.etc.path().join("bootstrap.json"),
            state_dir: self.state.path().to_path_buf(),
            root: self.root.path().to_path_buf(),
            allow_http: true,
            settle: Duration::ZERO,
            wait_seconds: 0,
        }
    }
}

#[test]
fn poll_apply_report_round_trip() {
    let desired = document(1, 1, &resources(), &signing_key());
    let server = service(desired);
    let node = Node::new(&server.url, TOKEN);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();

    assert_eq!(
        agent.poll_once().unwrap(),
        PollOutcome::Applied(Duration::ZERO)
    );
    assert_eq!(
        agent.poll_once().unwrap(),
        PollOutcome::Idle(Duration::ZERO)
    );

    let requests = server.requests();
    let paths: Vec<&str> = requests.iter().map(|r| r.path.as_str()).collect();
    assert_eq!(paths[0], "/s3cr3t/agent/v1/poll");
    assert!(paths[1].starts_with("/s3cr3t/agent/v1/blobs/"));
    assert_eq!(
        &paths[2..],
        ["/s3cr3t/agent/v1/report", "/s3cr3t/agent/v1/poll"]
    );
    assert_eq!(
        requests[0].json(),
        json!({"type": "poll", "have": null, "wait_seconds": 0})
    );
    assert_eq!(requests[3].json()["have"]["revision"], 1);

    let report = requests[2].json();
    assert_eq!(report["type"], "report");
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
    assert_eq!(
        report["health"]["checks"][0],
        json!({"name": "unit-netcore", "status": "pass", "message": "netcore.service is active"})
    );
    assert_eq!(report["health"]["status"], "degraded");
    assert_eq!(report["versions"]["xray"], "26.7.11");
    assert_eq!(report["versions"]["agent"], meridian_agent::VERSION);
    for key in ["realm", "nginx"] {
        assert!(
            report["versions"].get(key).is_some_and(Value::is_null),
            "{key} is sent as null"
        );
    }
    let text = String::from_utf8(requests[2].body.clone()).unwrap();
    assert!(!text.contains("3f0c8a52"), "reports never carry user UUIDs");

    // A restarted agent re-applies the held document without changing anything.
    drop(agent);
    exec.clear();
    let mut restarted = Agent::new(node.options(), &exec).unwrap();
    assert!(restarted.has_held());
    restarted.apply_held().unwrap();
    assert!(
        exec.calls()
            .iter()
            .all(|c| !c.contains("restart") && !c.contains(" start"))
    );
}

#[test]
fn a_converged_document_is_recorded_as_applied_and_same_content_bumps_only_the_revision() {
    let key = signing_key();
    let only_supported: Vec<Value> = resources().into_iter().take(4).collect();
    let server = service(document(2, 5, &only_supported, &key));
    let node = Node::new(&server.url, TOKEN);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    assert_eq!(agent.state.applied.as_ref().unwrap().revision, 5);

    // Delivered directly: a same-content revision 6 is recorded without touching the host.
    let server_b = service(document(2, 6, &only_supported, &key));
    let node_b_opts = {
        let mut opts = node.options();
        let mut boot = bootstrap_json(&format!("{}/s3cr3t", server_b.url), &key);
        boot["node_token"] = json!(TOKEN);
        let path = node.etc.path().join("bootstrap-b.json");
        std::fs::write(&path, serde_json::to_vec(&boot).unwrap()).unwrap();
        opts.bootstrap = path;
        opts
    };
    drop(agent);
    exec.clear();
    let mut agent = Agent::new(node_b_opts, &exec).unwrap();
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

    // An older revision is refused.
    let server_c = service(document(2, 4, &only_supported, &key));
    let mut boot = bootstrap_json(&format!("{}/s3cr3t", server_c.url), &key);
    boot["node_token"] = json!(TOKEN);
    let path = node.etc.path().join("bootstrap-c.json");
    std::fs::write(&path, serde_json::to_vec(&boot).unwrap()).unwrap();
    let mut opts = node.options();
    opts.bootstrap = path;
    drop(agent);
    let mut agent = Agent::new(opts, &exec).unwrap();
    assert_eq!(agent.poll_once().unwrap(), PollOutcome::Refused);
    assert_eq!(agent.state.held_ref().unwrap().revision, 6);
}

#[test]
fn a_document_signed_by_another_key_is_refused_and_nothing_is_held() {
    let other = ed25519_dalek::SigningKey::from_bytes(&[9u8; 32]);
    let server = service(document(1, 1, &resources(), &other));
    let node = Node::new(&server.url, TOKEN);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    assert_eq!(agent.poll_once().unwrap(), PollOutcome::Refused);
    assert!(!agent.has_held());
    assert!(exec.calls().is_empty());
}

#[test]
fn a_refused_token_surfaces_as_401() {
    let server = service(document(1, 1, &resources(), &signing_key()));
    let node = Node::new(&server.url, "wrong-token-0123456789abcdefghijklmnop");
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    assert!(matches!(
        agent.poll_once(),
        Err(HttpError::Status { status: 401, .. })
    ));
}

#[test]
fn a_held_document_from_a_replaced_key_is_dropped_but_the_ledger_and_epoch_floor_stay() {
    let server = service(document(4, 1, &resources(), &signing_key()));
    let node = Node::new(&server.url, TOKEN);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    drop(agent);

    let other = ed25519_dalek::SigningKey::from_bytes(&[9u8; 32]);
    let boot = bootstrap_json(&format!("{}/s3cr3t", server.url), &other);
    std::fs::write(
        node.etc.path().join("bootstrap.json"),
        serde_json::to_vec(&boot).unwrap(),
    )
    .unwrap();
    let agent = Agent::new(node.options(), &exec).unwrap();
    assert!(!agent.has_held());
    assert_eq!(agent.state.highest_epoch, Some(4));
    assert!(!agent.state.ledger.is_empty());
}

#[test]
fn a_malformed_state_file_stops_the_agent() {
    let node = Node::new("http://127.0.0.1:9", TOKEN);
    std::fs::write(node.state.path().join("state.json"), b"{not json").unwrap();
    let exec = FakeExec::default();
    let err = Agent::new(node.options(), &exec).err().unwrap();
    assert!(err.contains("malformed"), "{err}");
}
