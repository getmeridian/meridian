//! Driven by pytest's loopback core/client fixture when MERIDIAN_TEST_XRAY is available.

mod common;

use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::Path;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use common::{FakeExec, FakeFetch, parse};
use meridian_agent::apply::Engine;
use meridian_agent::model::ResourceObservation;
use meridian_agent::state::LedgerEntry;
use meridian_agent::system::{CmdOutput, Exec, RealExec};
use serde_json::Value;

struct CoreExec {
    host: FakeExec,
    root: PathBuf,
    process: Mutex<Option<Child>>,
}

impl Drop for CoreExec {
    fn drop(&mut self) {
        if let Some(mut child) = self.process.lock().unwrap().take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

impl Exec for CoreExec {
    fn run(
        &self,
        program: &str,
        args: &[&str],
        env: &[(&str, &str)],
    ) -> std::io::Result<CmdOutput> {
        if args.first() == Some(&"api")
            && args.get(1) == Some(&"adrules")
            && *self.host.routing_down.lock().unwrap()
        {
            return Ok(CmdOutput {
                success: false,
                stdout: String::new(),
                stderr: "secret-canary".into(),
            });
        }
        if program == "systemctl" || program == "journalctl" {
            let output = self.host.run(program, args, env)?;
            if program == "systemctl"
                && matches!(args.first(), Some(&"start") | Some(&"restart"))
                && output.success
            {
                let mut process = self.process.lock().unwrap();
                if let Some(mut child) = process.take() {
                    child.kill()?;
                    child.wait()?;
                }
                let child = Command::new(self.root.join("usr/local/lib/netagent/netcore"))
                    .args(["run", "-c"])
                    .arg(self.root.join("etc/netagent/core.json"))
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .spawn()?;
                self.host
                    .main_pids
                    .lock()
                    .unwrap()
                    .insert("netcore.service".into(), child.id());
                *process = Some(child);
            }
            return Ok(output);
        }
        self.host.calls.lock().unwrap().push(format!(
            "{} {}",
            Path::new(program).file_name().unwrap().to_string_lossy(),
            args.join(" ")
        ));
        RealExec.run(program, args, env)
    }
}

fn socks(port: u16, target: u16) -> std::io::Result<TcpStream> {
    let mut stream = TcpStream::connect(("127.0.0.1", port))?;
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    stream.set_write_timeout(Some(Duration::from_secs(2)))?;
    stream.write_all(&[5, 1, 0])?;
    let mut hello = [0; 2];
    stream.read_exact(&mut hello)?;
    if hello != [5, 0] {
        return Err(std::io::Error::other("SOCKS greeting refused"));
    }
    let mut request = vec![5, 1, 0, 1, 127, 0, 0, 1];
    request.extend_from_slice(&target.to_be_bytes());
    stream.write_all(&request)?;
    let mut response = [0; 10];
    stream.read_exact(&mut response)?;
    if response[1] != 0 {
        return Err(std::io::Error::other("SOCKS target refused"));
    }
    Ok(stream)
}

fn exchange(stream: &mut TcpStream) -> bool {
    let mut response = [0; 5];
    stream.write_all(b"probe").is_ok()
        && stream.read_exact(&mut response).is_ok()
        && response == *b"probe"
}

fn probe(port: u16, target: u16) -> bool {
    socks(port, target).is_ok_and(|mut stream| exchange(&mut stream))
}

fn warm(port: u16, target: u16) {
    let deadline = Instant::now() + Duration::from_secs(10);
    while !probe(port, target) {
        assert!(
            Instant::now() < deadline,
            "client could not reach the loopback target"
        );
        std::thread::sleep(Duration::from_millis(25));
    }
}

#[test]
fn removing_or_rotating_only_blocks_the_old_identitys_new_streams() {
    let Ok(file) = std::env::var("MERIDIAN_FLOW_FIXTURE") else {
        return;
    };
    let fixture: Value = serde_json::from_slice(&std::fs::read(file).unwrap()).unwrap();
    let get = |key: &str| fixture[key].as_str().unwrap();
    let binary = get("binary");
    let api = format!("--server={}", get("api"));
    let port = |key: &str| fixture[key].as_u64().unwrap() as u16;
    let target = port("target");
    let before: Vec<Value> = serde_json::from_value(fixture["before"].clone()).unwrap();
    let after: Vec<Value> = serde_json::from_value(fixture["after"].clone()).unwrap();
    let fetch = FakeFetch::default().with(
        "https://assets.example.org/xray",
        std::fs::read(binary).unwrap(),
    );
    let exec = CoreExec {
        host: FakeExec::default(),
        root: get("root").into(),
        process: Mutex::new(None),
    };
    let engine = Engine {
        root: get("root").into(),
        cache_dir: Path::new(get("root")).join("cache"),
        exec: &exec,
        fetch: &fetch,
        settle: Duration::ZERO,
        allow_http: false,
    };
    let mut ledger: Vec<LedgerEntry> = Vec::new();
    let mut observations: Vec<ResourceObservation> = Vec::new();
    let initial = engine.apply(
        &parse(&before),
        &mut ledger,
        &observations,
        "2026-11-01T12:00:00Z",
    );
    assert!(
        initial.converged && initial.flow_error.is_none(),
        "{initial:?}"
    );
    observations = initial.observations;
    eprintln!("warm initial sessions");
    warm(port("client_a"), target);
    warm(port("client_b"), target);

    // Reproduce the pinned core defect itself: password removal does not revoke
    // the authenticated QUIC/mux connection's ability to dispatch new streams.
    let tag = format!("-tag={}", get("tag"));
    let removed = RealExec
        .run(binary, &["api", "rmu", &api, &tag, get("email_a")], &[])
        .unwrap();
    assert!(
        removed.success && removed.stdout.contains("Removed 1 user(s)"),
        "{}",
        removed.stderr
    );
    assert!(
        probe(port("client_a"), target),
        "the pinned runtime no longer reproduces the defect; revisit this test"
    );
    if get("tag") == "hy2" {
        engine
            .add_hysteria_user(get("api"), get("tag"), get("email_a"), get("credential_a"))
            .unwrap();
    } else {
        let restored = RealExec
            .run(binary, &["api", "adu", &api, get("restore")], &[])
            .unwrap();
        assert!(restored.success && restored.stdout.contains("Added 1 user(s)"));
    }
    warm(port("client_a"), target);
    warm(port("client_b"), target);
    let mut transfer_a = socks(port("client_a"), target).unwrap();
    let mut transfer_b = socks(port("client_b"), target).unwrap();
    assert!(exchange(&mut transfer_a) && exchange(&mut transfer_b));
    exec.host.clear();
    let guard_failure = fixture["guard_failure"].as_bool().unwrap_or(false);
    *exec.host.routing_down.lock().unwrap() = guard_failure;
    let mut changed = engine.apply(
        &parse(&after),
        &mut ledger,
        &observations,
        "2026-11-01T12:00:01Z",
    );
    assert!(
        changed.converged && changed.flow_error.is_some() == guard_failure,
        "{changed:?}"
    );
    if guard_failure {
        assert!(
            probe(port("client_b"), target),
            "a failed install blocked an active person"
        );
        if fixture["rotation"].as_bool().unwrap() {
            warm(port("client_new"), target);
        }
        assert!(
            probe(port("client_a"), target),
            "the failed install must remain pending"
        );
        *exec.host.routing_down.lock().unwrap() = false;
        changed = engine.apply(
            &parse(&after),
            &mut ledger,
            &observations,
            "2026-11-01T12:00:02Z",
        );
        assert!(
            changed.converged && changed.flow_error.is_none(),
            "{changed:?}"
        );
    }
    observations = changed.observations;
    assert!(
        !probe(port("client_a"), target),
        "revoked identity opened another stream on its live session"
    );
    assert!(
        probe(port("client_b"), target),
        "another person's new stream was blocked"
    );
    assert!(
        exchange(&mut transfer_a),
        "the documented in-flight transfer boundary changed"
    );
    assert!(
        exchange(&mut transfer_b),
        "another person's transfer was interrupted"
    );
    assert!(
        exec.host
            .calls()
            .iter()
            .all(|call| !call.contains(" api rmi ") && !call.contains("systemctl restart"))
    );
    if fixture["rotation"].as_bool().unwrap() {
        warm(port("client_new"), target);
    }

    // Retry intent repairs missing runtime blocks while the same core still
    // holds authenticated sessions; rules never travel through core.json.
    assert!(
        RealExec
            .run(binary, &["api", "adrules", &api, get("base_routing")], &[])
            .unwrap()
            .success
    );
    assert!(probe(port("client_a"), target));
    let repaired = engine.apply(
        &parse(&after),
        &mut ledger,
        &observations,
        "2026-11-01T12:00:02Z",
    );
    assert!(
        repaired.converged && repaired.flow_error.is_none(),
        "{repaired:?}"
    );
    assert!(!probe(port("client_a"), target));
    assert!(probe(port("client_b"), target) && exchange(&mut transfer_b));
    if !fixture["rotation"].as_bool().unwrap() {
        *exec.host.routing_down.lock().unwrap() = true;
        let restored = engine.apply(
            &parse(&before),
            &mut ledger,
            &repaired.observations,
            "2026-11-01T12:00:03Z",
        );
        assert!(
            restored.converged && restored.flow_error.is_none(),
            "{restored:?}"
        );
        warm(port("client_a"), target);
        assert!(exchange(&mut transfer_b));
        *exec.host.routing_down.lock().unwrap() = false;
    }
    // The retained explicit reset is the all-sessions path, including in-flight
    // traffic belonging to the other person on this same inbound.
    let config: Value = serde_json::from_slice(
        &std::fs::read(Path::new(get("root")).join("etc/netagent/core.json")).unwrap(),
    )
    .unwrap();
    let reset = Path::new(get("root")).join("reset.json");
    std::fs::write(
        &reset,
        serde_json::to_vec(&serde_json::json!({"inbounds": [config["inbounds"][0]]})).unwrap(),
    )
    .unwrap();
    if get("tag") == "hy2" {
        assert!(
            RealExec
                .run(binary, &["api", "rmi", &api, get("tag")], &[])
                .unwrap()
                .success
        );
        assert!(
            RealExec
                .run(binary, &["api", "adi", &api, reset.to_str().unwrap()], &[])
                .unwrap()
                .success
        );
        assert!(!exchange(&mut transfer_a) && !exchange(&mut transfer_b));
    } else {
        // The pinned listener does not own accepted VLESS connections. A reset
        // cannot implement a hard cut-off; the service must request restart_unit.
        assert!(
            RealExec
                .run(binary, &["api", "rmi", &api, get("tag")], &[])
                .unwrap()
                .success
        );
        assert!(exchange(&mut transfer_a) && exchange(&mut transfer_b));
        assert!(
            exec.run("systemctl", &["restart", "netcore.service"], &[])
                .unwrap()
                .success
        );
        assert!(
            !exchange(&mut transfer_a) && !exchange(&mut transfer_b),
            "core restart left an in-flight transfer alive"
        );
        warm(port("client_b"), target);
    }
}

fn address_probe(port: u16, address: &str, target: u16) -> bool {
    let result = (|| -> std::io::Result<TcpStream> {
        let mut stream = TcpStream::connect(("127.0.0.1", port))?;
        stream.set_read_timeout(Some(Duration::from_secs(2)))?;
        stream.set_write_timeout(Some(Duration::from_secs(2)))?;
        stream.write_all(&[5, 1, 0])?;
        let mut hello = [0; 2];
        stream.read_exact(&mut hello)?;
        if hello != [5, 0] {
            return Err(std::io::Error::other("SOCKS greeting refused"));
        }
        let mut request = vec![5, 1, 0];
        if let Ok(ip) = address.parse::<std::net::Ipv4Addr>() {
            request.push(1);
            request.extend_from_slice(&ip.octets());
        } else {
            request.extend_from_slice(&[3, address.len() as u8]);
            request.extend_from_slice(address.as_bytes());
        }
        request.extend_from_slice(&target.to_be_bytes());
        stream.write_all(&request)?;
        let mut response = [0; 10];
        stream.read_exact(&mut response)?;
        if response[1] != 0 {
            return Err(std::io::Error::other("SOCKS destination refused"));
        }
        Ok(stream)
    })();
    result.is_ok_and(|mut stream| exchange(&mut stream))
}

#[test]
fn active_domains_keep_the_dns_routing_pass_after_revocation() {
    let Ok(file) = std::env::var("MERIDIAN_FLOW_ROUTING_FIXTURE") else {
        return;
    };
    let fixture: Value = serde_json::from_slice(&std::fs::read(file).unwrap()).unwrap();
    let get = |key: &str| fixture[key].as_str().unwrap();
    let target = fixture["target"].as_u64().unwrap() as u16;
    let client = fixture["client"].as_u64().unwrap() as u16;
    let before: Vec<Value> = serde_json::from_value(fixture["before"].clone()).unwrap();
    let after: Vec<Value> = serde_json::from_value(fixture["after"].clone()).unwrap();
    let fetch = FakeFetch::default()
        .with(
            "https://assets.example.org/xray",
            std::fs::read(get("binary")).unwrap(),
        )
        .with(
            "https://assets.example.org/geoip",
            std::fs::read(get("geoip")).unwrap(),
        );
    let exec = CoreExec {
        host: FakeExec::default(),
        root: get("root").into(),
        process: Mutex::new(None),
    };
    let engine = Engine {
        root: get("root").into(),
        cache_dir: Path::new(get("root")).join("cache"),
        exec: &exec,
        fetch: &fetch,
        settle: Duration::ZERO,
        allow_http: false,
    };
    let mut ledger = Vec::new();
    let initial = engine.apply(&parse(&before), &mut ledger, &[], "2026-11-01T12:00:00Z");
    assert!(
        initial.converged && initial.flow_error.is_none(),
        "{initial:?}"
    );
    let deadline = Instant::now() + Duration::from_secs(10);
    while !address_probe(client, "127.0.0.2", target) {
        assert!(
            Instant::now() < deadline,
            "the active client cannot reach the IP egress"
        );
        std::thread::sleep(Duration::from_millis(25));
    }
    let server = format!("--server={}", get("api"));
    let permit = RealExec
        .run(
            get("binary"),
            &["api", "adrules", &server, get("allow_private")],
            &[],
        )
        .unwrap();
    assert!(permit.success);
    assert!(
        address_probe(client, "private.example.org", target),
        "the private-target control cannot connect"
    );
    let restore = RealExec
        .run(
            get("binary"),
            &["api", "adrules", &server, get("base_routing")],
            &[],
        )
        .unwrap();
    assert!(restore.success);
    assert!(
        address_probe(client, "egress.example.org", target),
        "the IP domain control cannot connect"
    );
    let applied = engine.apply(
        &parse(&after),
        &mut ledger,
        &initial.observations,
        "2026-11-01T12:00:01Z",
    );
    assert!(
        applied.converged && applied.flow_error.is_none(),
        "{applied:?}"
    );
    assert!(
        address_probe(client, "127.0.0.2", target),
        "the IP egress stopped working"
    );
    assert!(
        !address_probe(client, "private.example.org", target),
        "the user guard bypasses geoip:private for a domain destination",
    );
    assert!(
        address_probe(client, "egress.example.org", target),
        "the user guard bypasses an IP egress for a domain destination",
    );
}
