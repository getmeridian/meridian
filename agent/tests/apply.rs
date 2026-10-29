//! The apply engine against a temporary host root, a fake systemctl/xray, and a fake fetcher.

mod common;

use std::os::unix::fs::PermissionsExt;
use std::time::Duration;

use common::*;
use meridian_agent::apply::{Engine, PassResult};
use meridian_agent::model::ResourceObservation;
use meridian_agent::state::LedgerEntry;
use serde_json::{Value, json};

const XRAY: &[u8] = b"\x7fELF fake xray";
const GEOIP: &[u8] = b"geoip data";
const GEOSITE: &[u8] = b"geosite data";
const ZIP_URL: &str = "https://github.example/Xray-linux-64.zip";
const UUID_A: &str = "3f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00";
const UUID_B: &str = "4f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00";

struct Host {
    root: TempDir,
    state: TempDir,
    exec: FakeExec,
    fetch: FakeFetch,
    ledger: Vec<LedgerEntry>,
    observations: Vec<ResourceObservation>,
}

impl Host {
    fn new(fetch: FakeFetch) -> Host {
        Host {
            root: host_root(),
            state: TempDir::new("state"),
            exec: FakeExec::default(),
            fetch,
            ledger: Vec::new(),
            observations: Vec::new(),
        }
    }

    fn apply(&mut self, resources: &[Value]) -> PassResult {
        let engine = Engine {
            root: self.root.path().to_path_buf(),
            cache_dir: self.state.path().join("cache"),
            exec: &self.exec,
            fetch: &self.fetch,
            settle: Duration::ZERO,
            allow_http: false,
        };
        let pass = engine.apply(
            &parse(resources),
            &mut self.ledger,
            &self.observations,
            "2026-10-29T12:00:00Z",
        );
        self.observations = pass.observations.clone();
        pass
    }

    fn read(&self, path: &str) -> Vec<u8> {
        std::fs::read(self.root.path().join(path.trim_start_matches('/'))).unwrap()
    }

    fn exists(&self, path: &str) -> bool {
        self.root.path().join(path.trim_start_matches('/')).exists()
    }

    fn mode(&self, path: &str) -> u32 {
        std::fs::metadata(self.root.path().join(path.trim_start_matches('/')))
            .unwrap()
            .permissions()
            .mode()
            & 0o7777
    }
}

fn status(pass: &PassResult, id: &str) -> (String, Option<String>) {
    let obs = pass.observations.iter().find(|o| o.id == id).unwrap();
    (
        obs.status.clone(),
        obs.error.as_ref().map(|e| e.code.clone()),
    )
}

fn zip() -> Vec<u8> {
    zip_archive(&[
        ("xray", XRAY),
        ("geoip.dat", GEOIP),
        ("geosite.dat", GEOSITE),
        ("LICENSE", b"MPL"),
    ])
}

fn full_document(vless: &[(&str, &str, &str)]) -> Vec<Value> {
    let archive = zip();
    vec![
        pin(
            "pin:xray",
            "xray",
            ZIP_URL,
            &archive,
            "zip",
            Some("xray"),
            "/usr/local/lib/netagent/netcore",
        ),
        pin(
            "pin:geoip",
            "geoip",
            ZIP_URL,
            &archive,
            "zip",
            Some("geoip.dat"),
            "/usr/local/lib/netagent/geoip.dat",
        ),
        pin(
            "pin:geosite",
            "geosite",
            ZIP_URL,
            &archive,
            "zip",
            Some("geosite.dat"),
            "/usr/local/lib/netagent/geosite.dat",
        ),
        file_inline("file:inline", "/etc/netagent/inline.conf", b"inline\n"),
        file_blob("file:blob", "/etc/netagent/keys/blob.key", b"secret blob\n"),
        xray_config(&["pin:xray", "pin:geoip", "pin:geosite"], None),
        vless_users(vless),
        hy2_users(&[("u-a", "0123456789abcdef0123")]),
        unit(
            "unit:netcore",
            "netcore.service",
            &["xray:config", "users:vless", "users:hy2"],
            &["xray:config", "users:vless", "users:hy2"],
        ),
    ]
}

fn fetcher() -> FakeFetch {
    FakeFetch::default()
        .with(ZIP_URL, zip())
        .service(b"secret blob\n".to_vec())
}

#[test]
fn a_full_document_applies_and_a_second_pass_changes_nothing() {
    let mut host = Host::new(fetcher());
    let doc = full_document(&[("u-a", UUID_A, "xtls-rprx-vision")]);
    let pass = host.apply(&doc);

    assert!(pass.converged, "{:?}", pass.observations);
    assert_eq!(
        host.fetch
            .requests()
            .iter()
            .filter(|r| r.as_str() == ZIP_URL)
            .count(),
        1,
        "zip downloaded once"
    );
    assert_eq!(host.read("/usr/local/lib/netagent/netcore"), XRAY);
    assert_eq!(host.mode("/usr/local/lib/netagent/netcore"), 0o755);
    assert_eq!(host.read("/usr/local/lib/netagent/geosite.dat"), GEOSITE);
    assert_eq!(host.read("/etc/netagent/keys/blob.key"), b"secret blob\n");
    assert_eq!(host.mode("/etc/netagent/keys/blob.key"), 0o600);
    assert_eq!(host.mode("/etc/netagent/inline.conf"), 0o640);
    assert!(
        !host.state.path().join("cache").exists(),
        "download cache removed"
    );

    let config: Value = serde_json::from_slice(&host.read("/etc/netagent/core.json")).unwrap();
    assert_eq!(
        config["inbounds"][0]["settings"]["clients"][0]["email"],
        "u-a"
    );
    assert_eq!(
        config["inbounds"][1]["settings"]["clients"][0]["auth"],
        "0123456789abcdef0123"
    );
    assert_eq!(host.mode("/etc/netagent/core.json"), 0o600);
    let asset = host
        .root
        .path()
        .join("usr/local/lib/netagent")
        .to_string_lossy()
        .into_owned();
    assert_eq!(*host.exec.asset_dirs.lock().unwrap(), vec![asset]);

    assert_eq!(
        host.exec.unit("netcore.service"),
        UnitState {
            enabled: true,
            active: true
        }
    );
    assert!(
        host.read("/etc/systemd/system/netcore.service")
            .starts_with(b"[Service]")
    );
    let obs = pass
        .observations
        .iter()
        .find(|o| o.id == "pin:xray")
        .unwrap();
    assert_eq!(
        obs.applied_sha256.as_deref(),
        Some(parse(&doc)[0].hash.as_str())
    );

    host.exec.clear();
    let again = host.apply(&doc);
    assert!(again.converged);
    assert!(again.changed.is_empty(), "{:?}", again.changed);
    assert_eq!(host.fetch.requests().len(), 2, "nothing downloaded again");
    let calls = host.exec.calls();
    assert!(
        calls
            .iter()
            .all(|c| !c.contains("restart") && !c.contains(" start") && !c.starts_with("netcore")),
        "{calls:?}"
    );
}

fn changed(pass: &PassResult) -> Vec<&str> {
    pass.changed.iter().map(String::as_str).collect()
}

fn clients(host: &Host, inbound: usize) -> Value {
    let config: Value = serde_json::from_slice(&host.read("/etc/netagent/core.json")).unwrap();
    config["inbounds"][inbound]["settings"]["clients"].clone()
}

#[test]
fn a_vless_user_change_goes_through_the_xray_api_without_a_restart() {
    let mut host = Host::new(fetcher());
    host.apply(&full_document(&[("u-a", UUID_A, "xtls-rprx-vision")]));
    host.exec.clear();

    let added = full_document(&[("u-a", UUID_A, "xtls-rprx-vision"), ("u-b", UUID_B, "")]);
    let pass = host.apply(&added);
    assert!(pass.converged, "{:?}", pass.observations);
    assert!(pass.changed.is_empty(), "{:?}", pass.changed);
    assert_eq!(host.exec.count("systemctl restart netcore.service"), 0);
    let calls = host.exec.calls();
    let api: Vec<&String> = calls
        .iter()
        .filter(|c| c.starts_with("netcore api"))
        .collect();
    assert_eq!(api.len(), 1, "{calls:?}");
    assert!(api[0].starts_with("netcore api adu --server=127.0.0.1:10085 "));
    let body = host.exec.added.lock().unwrap()[0].clone();
    assert_eq!(body["inbounds"][0]["tag"], "vless-reality");
    assert_eq!(
        body["inbounds"][0]["settings"]["clients"],
        json!([{"id": UUID_B, "email": "u-b"}]),
        "only the added user goes to adu"
    );
    assert!(
        !host.state.path().join("cache").exists(),
        "the adu file holding UUIDs is gone"
    );
    // The file on disk still carries every user, for the next restart.
    assert_eq!(clients(&host, 0)[1], json!({"id": UUID_B, "email": "u-b"}));

    // Removal and a changed UUID: remove both, add the new credentials back.
    host.exec.clear();
    let pass = host.apply(&full_document(&[("u-a", UUID_B, "xtls-rprx-vision")]));
    assert!(pass.changed.is_empty(), "{:?}", pass.changed);
    assert_eq!(
        host.exec
            .count("netcore api rmu --server=127.0.0.1:10085 -tag=vless-reality u-a u-b"),
        1,
        "{:?}",
        host.exec.calls()
    );
    assert_eq!(
        host.exec.added.lock().unwrap()[1]["inbounds"][0]["settings"]["clients"],
        json!([{"id": UUID_B, "email": "u-a", "flow": "xtls-rprx-vision"}])
    );
    assert_eq!(clients(&host, 0).as_array().unwrap().len(), 1);

    // Nothing left to do on the next pass.
    host.exec.clear();
    let again = host.apply(&full_document(&[("u-a", UUID_B, "xtls-rprx-vision")]));
    assert!(again.changed.is_empty());
    assert!(host.exec.calls().iter().all(|c| !c.contains(" api ")));
}

#[test]
fn a_failing_xray_api_falls_back_to_a_restart() {
    let mut host = Host::new(fetcher());
    host.apply(&full_document(&[("u-a", UUID_A, "xtls-rprx-vision")]));
    *host.exec.api_down.lock().unwrap() = true;
    host.exec.clear();

    let pass = host.apply(&full_document(&[
        ("u-a", UUID_A, "xtls-rprx-vision"),
        ("u-b", UUID_B, ""),
    ]));
    assert!(pass.converged);
    assert_eq!(
        changed(&pass),
        ["unit:netcore", "users:vless", "xray:config"]
    );
    assert_eq!(host.exec.count("systemctl restart netcore.service"), 1);
    assert_eq!(clients(&host, 0)[1], json!({"id": UUID_B, "email": "u-b"}));
}

#[test]
fn a_config_written_before_a_crash_still_restarts_its_unit() {
    let mut host = Host::new(fetcher());
    host.apply(&full_document(&[]));
    let saved = host.ledger.clone();
    let mut doc = full_document(&[]);
    doc[5]["config"]["log"]["loglevel"] = json!("info");
    host.apply(&doc);
    // The agent died after writing the config and before saving the ledger or restarting.
    host.ledger = saved;
    host.exec.clear();

    let pass = host.apply(&doc);
    assert!(pass.converged);
    assert_eq!(host.exec.count("systemctl restart netcore.service"), 1);
    host.exec.clear();
    host.apply(&doc);
    assert_eq!(host.exec.count("systemctl restart netcore.service"), 0);
}

#[test]
fn a_restart_that_failed_is_owed_until_it_succeeds() {
    let mut host = Host::new(fetcher());
    host.apply(&full_document(&[]));
    host.exec
        .stuck
        .lock()
        .unwrap()
        .insert("netcore.service".into());
    let mut doc = full_document(&[]);
    doc[5]["config"]["log"]["loglevel"] = json!("info");
    let pass = host.apply(&doc);
    assert_eq!(
        status(&pass, "unit:netcore"),
        ("failed".into(), Some("service_failed".into()))
    );
    // Still running the old config: the next pass must restart it, though nothing changes.
    assert!(host.exec.unit("netcore.service").active);
    host.exec.stuck.lock().unwrap().clear();
    host.exec.clear();

    let pass = host.apply(&doc);
    assert!(pass.converged);
    assert_eq!(host.exec.count("systemctl restart netcore.service"), 1);
    host.exec.clear();
    host.apply(&doc);
    assert_eq!(host.exec.count("systemctl restart netcore.service"), 0);
}

#[test]
fn hysteria2_users_change_live_and_config_changes_restart() {
    let mut host = Host::new(fetcher());
    host.apply(&full_document(&[("u-a", UUID_A, "xtls-rprx-vision")]));

    // Xray's adu cannot build Hysteria2 users: only the hy2 inbound is rebuilt, with all of them.
    host.exec.clear();
    let mut doc = full_document(&[("u-a", UUID_A, "xtls-rprx-vision")]);
    doc[7] = hy2_users(&[
        ("u-a", "0123456789abcdef0123"),
        ("u-b", "abcdef0123456789abcd"),
    ]);
    let pass = host.apply(&doc);
    assert!(
        pass.converged && pass.changed.is_empty(),
        "{:?}",
        pass.changed
    );
    assert_eq!(host.exec.count("systemctl restart netcore.service"), 0);
    assert_eq!(
        host.exec
            .count("netcore api rmi --server=127.0.0.1:10085 hy2"),
        1,
        "{:?}",
        host.exec.calls()
    );
    let rebuilt = host.exec.rebuilt.lock().unwrap()[0].clone();
    assert_eq!(rebuilt["inbounds"][0]["tag"], "hy2");
    assert_eq!(
        rebuilt["inbounds"][0]["settings"]["clients"],
        clients(&host, 1)
    );
    assert_eq!(clients(&host, 1).as_array().unwrap().len(), 2);
    assert!(
        !host
            .state
            .path()
            .join("cache")
            .join("adi-hy2.json")
            .exists(),
        "the adi file holding credentials is gone"
    );

    // A removal alone goes through rmu, without touching the inbound.
    host.exec.clear();
    let mut doc = full_document(&[("u-a", UUID_A, "xtls-rprx-vision")]);
    doc[7] = hy2_users(&[("u-b", "abcdef0123456789abcd")]);
    let pass = host.apply(&doc);
    assert!(pass.changed.is_empty(), "{:?}", pass.changed);
    assert_eq!(
        host.exec
            .count("netcore api rmu --server=127.0.0.1:10085 -tag=hy2 u-a"),
        1,
        "{:?}",
        host.exec.calls()
    );
    assert!(host.exec.calls().iter().all(|c| !c.contains(" api rmi ")));

    // An API that fails mid-rebuild falls back to one restart from the file.
    *host.exec.api_down.lock().unwrap() = true;
    host.exec.clear();
    let mut doc = full_document(&[("u-a", UUID_A, "xtls-rprx-vision")]);
    doc[7] = hy2_users(&[
        ("u-b", "abcdef0123456789abcd"),
        ("u-c", "fedcba9876543210fedc"),
    ]);
    let pass = host.apply(&doc);
    assert_eq!(changed(&pass), ["unit:netcore", "users:hy2", "xray:config"]);
    assert_eq!(host.exec.count("systemctl restart netcore.service"), 1);
    *host.exec.api_down.lock().unwrap() = false;

    // A config change rides along with a user change: one restart, no API.
    host.exec.clear();
    let mut doc = full_document(&[("u-b", UUID_B, "")]);
    doc[7] = hy2_users(&[
        ("u-b", "abcdef0123456789abcd"),
        ("u-c", "fedcba9876543210fedc"),
    ]);
    doc[5]["config"]["log"]["loglevel"] = json!("info");
    let pass = host.apply(&doc);
    assert_eq!(
        changed(&pass),
        ["unit:netcore", "users:vless", "xray:config"]
    );
    assert_eq!(host.exec.count("systemctl restart netcore.service"), 1);
    assert!(host.exec.calls().iter().all(|c| !c.contains(" api ")));
}

#[test]
fn a_stopped_xray_gets_the_new_users_from_its_file() {
    let mut host = Host::new(fetcher());
    host.apply(&full_document(&[("u-a", UUID_A, "xtls-rprx-vision")]));
    host.exec
        .units
        .lock()
        .unwrap()
        .get_mut("netcore.service")
        .unwrap()
        .active = false;
    host.exec.clear();

    let pass = host.apply(&full_document(&[("u-b", UUID_B, "")]));
    assert!(pass.converged);
    assert!(host.exec.calls().iter().all(|c| !c.contains(" api ")));
    assert!(host.exec.unit("netcore.service").active);
    assert_eq!(clients(&host, 0), json!([{"id": UUID_B, "email": "u-b"}]));
}

#[test]
fn drift_is_repaired_on_the_next_pass() {
    let mut host = Host::new(fetcher());
    let doc = full_document(&[]);
    host.apply(&doc);
    std::fs::write(
        host.root.path().join("etc/netagent/inline.conf"),
        b"tampered",
    )
    .unwrap();
    std::fs::write(
        host.root.path().join("usr/local/lib/netagent/netcore"),
        b"tampered",
    )
    .unwrap();
    host.exec
        .units
        .lock()
        .unwrap()
        .get_mut("netcore.service")
        .unwrap()
        .active = false;

    let pass = host.apply(&doc);
    assert!(pass.converged);
    assert_eq!(host.read("/etc/netagent/inline.conf"), b"inline\n");
    assert_eq!(host.read("/usr/local/lib/netagent/netcore"), XRAY);
    assert!(pass.changed.contains("file:inline") && pass.changed.contains("pin:xray"));
    assert!(host.exec.unit("netcore.service").active);
}

#[test]
fn failures_block_their_dependents() {
    let mut host =
        Host::new(fetcher().with("https://bad.example/x", b"not what was pinned".to_vec()));
    let mut bad_pin = pin(
        "pin:bad",
        "realm",
        "https://bad.example/x",
        b"pinned bytes",
        "none",
        None,
        "/usr/local/lib/netagent/realm",
    );
    bad_pin["size"] = json!(19);
    let doc = vec![
        json!({"kind": "firewall_rule", "id": "fw:443", "depends_on": [], "port": 443, "protocol": "tcp", "allow_from": []}),
        unit("unit:after-fw", "netfw.service", &["fw:443"], &[]),
        file_inline("file:escape", "/etc/shadow", b"x"),
        unit("unit:agent", "netagent.service", &[], &[]),
        bad_pin,
        unit("unit:realm", "netfwd-a.service", &["pin:bad"], &["pin:bad"]),
    ];
    let pass = host.apply(&doc);
    assert!(!pass.converged);
    assert_eq!(
        status(&pass, "fw:443"),
        ("failed".into(), Some("unsupported".into()))
    );
    assert_eq!(status(&pass, "unit:after-fw"), ("blocked".into(), None));
    assert_eq!(
        status(&pass, "file:escape"),
        ("failed".into(), Some("path_not_allowed".into()))
    );
    assert_eq!(
        status(&pass, "unit:agent"),
        ("failed".into(), Some("path_not_allowed".into()))
    );
    assert_eq!(
        status(&pass, "pin:bad"),
        ("failed".into(), Some("hash_mismatch".into()))
    );
    assert_eq!(status(&pass, "unit:realm"), ("blocked".into(), None));
    assert!(!host.root.path().join("etc/shadow").exists());
    assert!(pass.observations.iter().all(|o| o.applied_sha256.is_none()));
}

#[test]
fn a_rejected_config_keeps_the_old_file_and_blocks_users_and_unit() {
    let mut host = Host::new(fetcher());
    host.apply(&full_document(&[]));
    let before = host.read("/etc/netagent/core.json");

    let mut doc = full_document(&[]);
    doc[5]["config"]["outbounds"][0]["protocol"] = json!("reject-me");
    let pass = host.apply(&doc);
    assert_eq!(
        status(&pass, "xray:config"),
        ("failed".into(), Some("validation_failed".into()))
    );
    assert_eq!(status(&pass, "users:vless"), ("blocked".into(), None));
    assert_eq!(status(&pass, "unit:netcore"), ("blocked".into(), None));
    assert_eq!(host.read("/etc/netagent/core.json"), before);
    let message = &pass.observations[5].error.as_ref().unwrap().message;
    assert!(message.contains("unknown protocol"), "{message}");
    // The previously applied version is still what applied_sha256 names.
    assert!(pass.observations[5].applied_sha256.is_some());
    let leftovers: Vec<_> = std::fs::read_dir(host.root.path().join("etc/netagent"))
        .unwrap()
        .flatten()
        .filter(|e| e.file_name().to_string_lossy().starts_with('.'))
        .collect();
    assert!(leftovers.is_empty(), "temp files cleaned up");
}

#[test]
fn a_unit_that_dies_after_start_fails_with_its_journal() {
    let mut host = Host::new(fetcher());
    host.exec
        .crashing
        .lock()
        .unwrap()
        .insert("netcore.service".into());
    let pass = host.apply(&full_document(&[]));
    assert_eq!(
        status(&pass, "unit:netcore"),
        ("failed".into(), Some("service_failed".into()))
    );
    let message = &pass
        .observations
        .last()
        .unwrap()
        .error
        .as_ref()
        .unwrap()
        .message;
    assert!(
        message.contains("address in use") && message.len() <= 400,
        "{message}"
    );
}

#[test]
fn vanished_resources_are_retired_dependents_first() {
    let mut host = Host::new(fetcher());
    let archive = tar_gz(&[("realm", b"realm binary")]);
    host.fetch
        .blobs
        .insert("https://realm.example/realm.tar.gz".into(), archive.clone());
    let first = vec![
        pin(
            "pin:realm",
            "realm",
            "https://realm.example/realm.tar.gz",
            &archive,
            "tar.gz",
            Some("realm"),
            "/usr/local/lib/netagent/realm",
        ),
        file_inline("file:old", "/etc/netagent/old.conf", b"old"),
        file_inline("file:moved", "/etc/netagent/kept.conf", b"kept"),
        unit(
            "unit:fwd",
            "netfwd-a.service",
            &["pin:realm", "file:old"],
            &["file:old"],
        ),
    ];
    assert!(host.apply(&first).converged);
    assert_eq!(host.read("/usr/local/lib/netagent/realm"), b"realm binary");
    host.exec.clear();

    // Same path under a new ID must survive the old ID's retirement.
    let second = vec![file_inline(
        "file:renamed",
        "/etc/netagent/kept.conf",
        b"kept",
    )];
    let pass = host.apply(&second);
    assert!(pass.converged);
    assert!(!host.exists("/etc/netagent/old.conf"));
    assert!(!host.exists("/usr/local/lib/netagent/realm"));
    assert!(!host.exists("/etc/systemd/system/netfwd-a.service"));
    assert_eq!(host.read("/etc/netagent/kept.conf"), b"kept");
    assert_eq!(
        host.exec.calls(),
        [
            "systemctl stop netfwd-a.service",
            "systemctl disable netfwd-a.service",
            "systemctl is-active netfwd-a.service",
            "systemctl daemon-reload"
        ]
    );
    let ids: Vec<&str> = host.ledger.iter().map(|e| e.id.as_str()).collect();
    assert_eq!(ids, ["file:renamed"]);
}

#[test]
fn a_unit_that_will_not_stop_keeps_the_document_unconverged() {
    let mut host = Host::new(fetcher());
    let kept = file_inline("file:kept", "/etc/netagent/kept.conf", b"kept");
    let first = vec![
        kept.clone(),
        unit("unit:fwd", "netfwd-a.service", &["file:kept"], &[]),
    ];
    assert!(host.apply(&first).converged);
    host.exec
        .stuck
        .lock()
        .unwrap()
        .insert("netfwd-a.service".into());

    let pass = host.apply(std::slice::from_ref(&kept));
    assert!(!pass.converged, "{:?}", pass.observations);
    assert_eq!(pass.unretired, ["unit:fwd"]);
    assert!(host.exists("/etc/systemd/system/netfwd-a.service"));
    assert!(host.exec.unit("netfwd-a.service").active);

    host.exec.stuck.lock().unwrap().clear();
    let pass = host.apply(&[kept]);
    assert!(pass.converged && pass.unretired.is_empty());
    assert!(!host.exists("/etc/systemd/system/netfwd-a.service"));
}
