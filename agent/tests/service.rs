//! The Rust agent against the real Python control service: enroll → hello → poll →
//! apply → report → stats, then the service's own traffic totals.
//!
//! Needs `uv` and the repository's Python environment; skipped (with a note) without them.

mod common;

use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::time::Duration;

use common::*;
use meridian_agent::agent::{Agent, Options, PollOutcome};
use serde_json::{Value, json};

const XRAY: &[u8] = b"\x7fELF fake xray";

struct PythonService {
    child: Child,
    stdin: Option<ChildStdin>,
    stdout: BufReader<ChildStdout>,
    hello: Value,
}

impl PythonService {
    fn start(workdir: &Path) -> Option<PythonService> {
        let repo = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
        let spawned = Command::new("uv")
            .args(["run", "--quiet", "python"])
            .arg(repo.join("agent/tests/fixtures/control_server.py"))
            .arg(workdir)
            .current_dir(repo)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn();
        let mut child = match spawned {
            Ok(child) => child,
            Err(err) => {
                eprintln!("skipping the Python round trip: cannot run uv ({err})");
                return None;
            }
        };
        let stdin = child.stdin.take();
        let mut stdout = BufReader::new(child.stdout.take().unwrap());
        let mut line = String::new();
        stdout.read_line(&mut line).unwrap();
        let hello = serde_json::from_str(&line)
            .unwrap_or_else(|err| panic!("control_server.py printed {line:?}: {err}"));
        Some(PythonService {
            child,
            stdin,
            stdout,
            hello,
        })
    }

    fn ask(&mut self, command: &str) -> Value {
        let stdin = self.stdin.as_mut().unwrap();
        writeln!(stdin, "{command}").unwrap();
        stdin.flush().unwrap();
        let mut line = String::new();
        self.stdout.read_line(&mut line).unwrap();
        serde_json::from_str(&line).unwrap()
    }
}

impl Drop for PythonService {
    fn drop(&mut self) {
        drop(self.stdin.take());
        let _ = self.child.wait();
    }
}

#[test]
fn the_rust_agent_enrolls_applies_and_counts_traffic_against_the_python_service() {
    let work = TempDir::new("python-service");
    let Some(mut service) = PythonService::start(work.path()) else {
        return;
    };
    let root = host_root();
    let state = TempDir::new("state");
    let bootstrap = work.path().join("bootstrap.json");
    std::fs::write(
        &bootstrap,
        serde_json::to_vec(&service.hello["bootstrap"]).unwrap(),
    )
    .unwrap();

    let exec = FakeExec::default();
    let options = Options {
        bootstrap: bootstrap.clone(),
        state_dir: state.path().to_path_buf(),
        root: root.path().to_path_buf(),
        allow_http: true,
        settle: Duration::ZERO,
        wait_seconds: 0,
        tls_ca_file: None,
    };
    let mut agent = Agent::new(options, &exec).unwrap();
    let outcome = agent.poll_once().unwrap();
    assert_eq!(outcome, PollOutcome::Applied(Duration::ZERO));
    assert!(agent.is_enrolled());
    let spent: Value = serde_json::from_slice(&std::fs::read(&bootstrap).unwrap()).unwrap();
    assert_eq!(spent["enrollment_token"], Value::Null);

    // The Python CA certified the Rust CSR, the Rust agent verified the Python signature,
    // and the document carries the service-composed user.
    let user_id = service.hello["user_id"].as_str().unwrap().to_string();
    let config: Value =
        serde_json::from_slice(&std::fs::read(root.path().join("etc/netagent/core.json")).unwrap())
            .unwrap();
    assert_eq!(
        config["inbounds"][0]["settings"]["clients"][0]["email"],
        user_id
    );
    let installed = root.path().join("usr/local/lib/netagent/netcore");
    assert_eq!(std::fs::read(installed).unwrap(), XRAY);
    let node = service.ask("node");
    assert_eq!(node["agent_version"], meridian_agent::VERSION);
    assert_eq!(node["applied"]["revision"], 1, "{node}");

    // A new user reaches the running Xray through its API, without a restart.
    let bob = service.ask("user bob");
    exec.clear();
    assert_eq!(
        agent.poll_once().unwrap(),
        PollOutcome::Applied(Duration::ZERO)
    );
    assert_eq!(exec.count("systemctl restart netcore.service"), 0);
    let added = exec.added.lock().unwrap().clone();
    assert_eq!(
        added[0]["inbounds"][0]["settings"]["clients"][0]["email"],
        bob["user_id"]
    );
    assert_eq!(service.ask("node")["applied"]["revision"], 2);

    *exec.stats_output.lock().unwrap() = json!({"stat": [
        {"name": format!("user>>>{user_id}>>>traffic>>>uplink"), "value": "1200"},
        {"name": format!("user>>>{user_id}>>>traffic>>>downlink"), "value": "34000"},
    ]})
    .to_string();
    assert!(agent.send_stats().unwrap());
    // A resend of the same totals under a new seq adds nothing.
    assert!(agent.send_stats().unwrap());

    let traffic = service.ask("traffic");
    let alice = &traffic["users"][0];
    assert_eq!(alice["username"], "alice");
    assert_eq!(alice["uplink_bytes"], 1200);
    assert_eq!(alice["downlink_bytes"], 34000);
    assert!(alice["last_seen_at"].is_string());
}
