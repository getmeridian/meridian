//! The run lock and the staged check/rename/restart flow against real local processes.

mod common;

use std::io::{BufRead, BufReader};
use std::path::Path;
use std::process::{Child, ChildStderr, Command, Stdio};

use common::pki::TestPki;
use common::{TempDir, bootstrap_json};
use serde_json::Value;

const BINARY: &str = env!("CARGO_BIN_EXE_netagent");

struct RunningAgent {
    child: Option<Child>,
    _stderr: BufReader<ChildStderr>,
}

impl Drop for RunningAgent {
    fn drop(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn command(binary: &Path, mode: &str, bootstrap: &Path, state: &Path) -> Command {
    let mut command = Command::new(binary);
    command
        .arg(mode)
        .arg("--bootstrap")
        .arg(bootstrap)
        .arg("--state-dir")
        .arg(state)
        .env_remove("NETAGENT_TLS_CA_FILE")
        .env_remove("NETAGENT_DEBUG")
        .stdin(Stdio::null());
    command
}

fn start(binary: &Path, bootstrap: &Path, state: &Path) -> RunningAgent {
    let mut child = command(binary, "run", bootstrap, state)
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let stderr = BufReader::new(child.stderr.take().unwrap());
    let mut running = RunningAgent {
        child: Some(child),
        _stderr: stderr,
    };
    let mut line = String::new();
    running._stderr.read_line(&mut line).unwrap();
    assert!(line.contains("starting for node"), "{line}");
    running
}

fn bootstrap(dir: &Path) -> std::path::PathBuf {
    let path = dir.join("bootstrap.json");
    // No token or credentials: run remains alive without dialing any service.
    let mut boot = bootstrap_json("https://service.example/s3cr3t", &TestPki::new());
    boot["enrollment_token"] = Value::Null;
    std::fs::write(&path, serde_json::to_vec(&boot).unwrap()).unwrap();
    path
}

#[test]
fn a_second_run_is_refused_before_loading_the_bootstrap() {
    let dir = TempDir::new("single-instance");
    let state = dir.path().join("state");
    let boot = bootstrap(dir.path());
    let _running = start(Path::new(BINARY), &boot, &state);
    let invalid = dir.path().join("invalid.json");
    std::fs::write(&invalid, "not JSON").unwrap();
    let result = command(Path::new(BINARY), "run", &invalid, &state)
        .output()
        .unwrap();
    assert!(!result.status.success());
    let error = String::from_utf8(result.stderr).unwrap();
    assert!(error.contains("another netagent instance"), "{error}");
    assert!(
        !error.contains("bootstrap"),
        "locking precedes all file validation: {error}"
    );
}

#[test]
fn a_staged_check_and_restart_work_while_the_old_agent_holds_the_lock() {
    let dir = TempDir::new("instance-upgrade");
    let state = dir.path().join("state");
    let boot = bootstrap(dir.path());
    let running = start(Path::new(BINARY), &boot, &state);
    let staged = dir.path().join("netagent.new");
    std::fs::copy(BINARY, &staged).unwrap();
    let checked = command(&staged, "check", &boot, &state).output().unwrap();
    assert!(
        checked.status.success(),
        "{}",
        String::from_utf8_lossy(&checked.stderr)
    );
    let report: Value = serde_json::from_slice(&checked.stdout).unwrap();
    assert_eq!(report["node_id"], "exit-1");
    assert_eq!(report["enrolled"], false);
    assert_eq!(report["version"], meridian_agent::VERSION);

    let installed = dir.path().join("netagent");
    std::fs::rename(&staged, &installed).unwrap();
    // systemd restart stops/reaps the old process before executing the new binary.
    drop(running);
    let _restarted = start(&installed, &boot, &state);
    let invalid = dir.path().join("invalid.json");
    std::fs::write(&invalid, "not JSON").unwrap();
    let result = command(Path::new(BINARY), "run", &invalid, &state)
        .output()
        .unwrap();
    assert!(String::from_utf8_lossy(&result.stderr).contains("another netagent instance"));
    assert!(!result.status.success());
}
