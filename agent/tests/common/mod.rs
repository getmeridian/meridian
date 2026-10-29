//! Shared doubles for the integration tests: temp roots, a fake host, a fake fetcher,
//! a mock HTTP service, and document signing.
#![allow(dead_code)]

use std::collections::{HashMap, HashSet};
use std::io::{self, BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD;
use ed25519_dalek::{Signer, SigningKey};
use meridian_agent::fetch::Fetch;
use meridian_agent::jcs;
use meridian_agent::model::Resource;
use meridian_agent::system::{CmdOutput, Exec};
use meridian_agent::verify::signed_bytes;
use serde_json::{Value, json};

pub const FLEET_ID: &str = "0b6f3c1e-8f6a-4d2b-9c41-7a5e2d9f0c13";
pub const NODE_ID: &str = "exit-1";
pub const TOKEN: &str = "node-token-0123456789abcdefghijklmnopqrstuvwxyz";

static COUNTER: AtomicUsize = AtomicUsize::new(0);

/// A temporary directory removed on drop.
pub struct TempDir(pub PathBuf);

impl TempDir {
    pub fn new(label: &str) -> TempDir {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        let path =
            std::env::temp_dir().join(format!("netagent-{label}-{}-{n}", std::process::id()));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(&path).unwrap();
        TempDir(path)
    }

    pub fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// A host root whose passwd/group map `root` to the current user, so chown is a no-op.
pub fn host_root() -> TempDir {
    let dir = TempDir::new("root");
    let meta = std::fs::metadata(dir.path()).unwrap();
    std::fs::create_dir_all(dir.path().join("etc")).unwrap();
    std::fs::write(
        dir.path().join("etc/passwd"),
        format!("root:x:{}:{}::/root:/bin/sh\n", meta.uid(), meta.gid()),
    )
    .unwrap();
    std::fs::write(
        dir.path().join("etc/group"),
        format!("root:x:{}:\n", meta.gid()),
    )
    .unwrap();
    dir
}

// ---- Fake host ----------------------------------------------------------------

#[derive(Default, Clone, Copy, Debug, PartialEq)]
pub struct UnitState {
    pub enabled: bool,
    pub active: bool,
}

/// systemctl, journalctl, and xray stand-ins. Xray rejects configs containing `"reject-me"`.
#[derive(Default)]
pub struct FakeExec {
    pub calls: Mutex<Vec<String>>,
    pub units: Mutex<HashMap<String, UnitState>>,
    pub crashing: Mutex<HashSet<String>>,
    pub asset_dirs: Mutex<Vec<String>>,
}

impl FakeExec {
    pub fn calls(&self) -> Vec<String> {
        self.calls.lock().unwrap().clone()
    }

    pub fn clear(&self) {
        self.calls.lock().unwrap().clear();
    }

    pub fn unit(&self, name: &str) -> UnitState {
        self.units
            .lock()
            .unwrap()
            .get(name)
            .copied()
            .unwrap_or_default()
    }

    pub fn count(&self, needle: &str) -> usize {
        self.calls().iter().filter(|c| c.as_str() == needle).count()
    }
}

fn ok(stdout: &str) -> io::Result<CmdOutput> {
    Ok(CmdOutput {
        success: true,
        stdout: stdout.to_string(),
        stderr: String::new(),
    })
}

fn fail(stdout: &str, stderr: &str) -> io::Result<CmdOutput> {
    Ok(CmdOutput {
        success: false,
        stdout: stdout.to_string(),
        stderr: stderr.to_string(),
    })
}

impl Exec for FakeExec {
    fn run(&self, program: &str, args: &[&str], env: &[(&str, &str)]) -> io::Result<CmdOutput> {
        let base = Path::new(program)
            .file_name()
            .unwrap()
            .to_string_lossy()
            .into_owned();
        self.calls
            .lock()
            .unwrap()
            .push(format!("{base} {}", args.join(" ")));
        match base.as_str() {
            "systemctl" => {
                let mut units = self.units.lock().unwrap();
                let name = args.get(1).copied().unwrap_or_default().to_string();
                let unit = units.entry(name.clone()).or_default();
                match args[0] {
                    "daemon-reload" => ok(""),
                    "is-enabled" if unit.enabled => ok("enabled\n"),
                    "is-enabled" => fail("disabled\n", ""),
                    "is-active" if unit.active => ok("active\n"),
                    "is-active" => fail("inactive\n", ""),
                    "enable" => {
                        unit.enabled = true;
                        ok("")
                    }
                    "disable" => {
                        unit.enabled = false;
                        ok("")
                    }
                    "start" | "restart" => {
                        unit.active = !self.crashing.lock().unwrap().contains(&name);
                        ok("")
                    }
                    "stop" => {
                        unit.active = false;
                        ok("")
                    }
                    other => panic!("unexpected systemctl verb {other}"),
                }
            }
            "journalctl" => ok("starting\nfailed to listen on 0.0.0.0:443: address in use\n"),
            _ if args.first() == Some(&"run") => {
                if let Some((_, dir)) = env.iter().find(|(k, _)| *k == "XRAY_LOCATION_ASSET") {
                    self.asset_dirs.lock().unwrap().push(dir.to_string());
                }
                let text = std::fs::read_to_string(args[3])?;
                serde_json::from_str::<Value>(&text).expect("xray -test receives JSON");
                if text.contains("\"reject-me\"") {
                    fail("", "Failed to start: infra/conf: unknown protocol\n")
                } else {
                    ok("Configuration OK.\n")
                }
            }
            other => panic!("unexpected program {other}"),
        }
    }

    fn sleep(&self, _duration: Duration) {}
}

/// Serves artifacts by source (`service` keys are `service:<sha256>`), counting requests.
#[derive(Default)]
pub struct FakeFetch {
    pub blobs: HashMap<String, Vec<u8>>,
    pub requests: Mutex<Vec<String>>,
}

impl FakeFetch {
    pub fn with(mut self, source: &str, bytes: Vec<u8>) -> FakeFetch {
        self.blobs.insert(source.to_string(), bytes);
        self
    }

    pub fn service(self, bytes: Vec<u8>) -> FakeFetch {
        let key = format!("service:{}", jcs::sha256_hex(&bytes));
        self.with(&key, bytes)
    }

    pub fn requests(&self) -> Vec<String> {
        self.requests.lock().unwrap().clone()
    }
}

impl Fetch for FakeFetch {
    fn fetch(
        &self,
        source: &str,
        sha256: &str,
        limit: u64,
        out: &mut dyn Write,
    ) -> Result<(), String> {
        let key = if source == "service" {
            format!("service:{sha256}")
        } else {
            source.to_string()
        };
        self.requests.lock().unwrap().push(key.clone());
        let bytes = self.blobs.get(&key).ok_or("404")?;
        let take = bytes.len().min(limit as usize + 1);
        out.write_all(&bytes[..take]).map_err(|err| err.to_string())
    }
}

// ---- Documents ----------------------------------------------------------------

pub fn signing_key() -> SigningKey {
    SigningKey::from_bytes(&[7u8; 32])
}

pub fn bootstrap_json(service_url: &str, key: &SigningKey) -> Value {
    json!({
        "protocol": "meridian.agent/v1",
        "service_url": service_url,
        "fleet_id": FLEET_ID,
        "node_id": NODE_ID,
        "node_token": TOKEN,
        "service_public_key": STANDARD.encode(key.verifying_key().to_bytes()),
    })
}

/// A signed desired-state document over `resources`.
pub fn document(epoch: u64, revision: u64, resources: &[Value], key: &SigningKey) -> Value {
    let resources = Value::Array(resources.to_vec());
    let mut doc = json!({
        "protocol": "meridian.agent/v1",
        "fleet_id": FLEET_ID,
        "node_id": NODE_ID,
        "fleet_epoch": epoch,
        "revision": revision,
        "sha256": jcs::canonical_sha256(&resources).unwrap(),
        "issued_at": "2026-10-29T12:00:00Z",
        "resources": resources,
    });
    let signature = key.sign(&signed_bytes(&doc).unwrap());
    doc["signature"] = json!({
        "key_id": jcs::sha256_hex(&key.verifying_key().to_bytes()),
        "signature": STANDARD.encode(signature.to_bytes()),
    });
    doc
}

pub fn parse(resources: &[Value]) -> Vec<Resource> {
    resources
        .iter()
        .map(|r| Resource::parse(r).unwrap())
        .collect()
}

// ---- Resource builders ----------------------------------------------------------

pub fn file_inline(id: &str, path: &str, content: &[u8]) -> Value {
    json!({
        "kind": "file", "id": id, "depends_on": [], "path": path, "mode": "0640",
        "owner": "root", "group": "root", "size": content.len(),
        "sha256": jcs::sha256_hex(content), "content_b64": STANDARD.encode(content), "sensitive": false,
    })
}

pub fn file_blob(id: &str, path: &str, content: &[u8]) -> Value {
    let mut value = file_inline(id, path, content);
    value["content_b64"] = Value::Null;
    value["sensitive"] = json!(true);
    value["mode"] = json!("0600");
    value
}

pub fn pin(
    id: &str,
    name: &str,
    source: &str,
    artifact: &[u8],
    archive: &str,
    member: Option<&str>,
    path: &str,
) -> Value {
    json!({
        "kind": "binary_pin", "id": id, "depends_on": [], "name": name, "version": "26.7.11",
        "sources": [source], "size": artifact.len(), "sha256": jcs::sha256_hex(artifact),
        "archive": archive, "member": member, "install_path": path, "mode": "0755",
    })
}

pub fn xray_config(depends_on: &[&str], extra_protocol: Option<&str>) -> Value {
    let mut inbounds = vec![
        json!({"tag": "vless-reality", "port": 443, "protocol": "vless", "settings": {"clients": [], "decryption": "none"}}),
        json!({"tag": "hy2", "port": 443, "protocol": "hysteria2", "settings": {"clients": []}}),
    ];
    if let Some(protocol) = extra_protocol {
        inbounds.push(json!({"tag": "extra", "port": 8443, "protocol": protocol}));
    }
    json!({
        "kind": "xray_config", "id": "xray:config", "depends_on": depends_on,
        "path": "/etc/netagent/core.json", "api_address": "127.0.0.1:10085",
        "config": {"log": {"loglevel": "warning"}, "inbounds": inbounds, "outbounds": [{"protocol": "freedom"}]},
    })
}

pub fn vless_users(users: &[(&str, &str, &str)]) -> Value {
    let users: Vec<Value> = users
        .iter()
        .map(|(user_id, uuid, flow)| json!({"user_id": user_id, "uuid": uuid, "flow": flow}))
        .collect();
    json!({"kind": "xray_users", "id": "users:vless", "depends_on": ["xray:config"], "inbound_tag": "vless-reality", "users": users})
}

pub fn hy2_users(users: &[(&str, &str)]) -> Value {
    let users: Vec<Value> = users
        .iter()
        .map(|(user_id, auth)| json!({"user_id": user_id, "auth": auth}))
        .collect();
    json!({"kind": "hysteria2_users", "id": "users:hy2", "depends_on": ["xray:config"], "inbound_tag": "hy2", "users": users})
}

pub fn unit(id: &str, name: &str, depends_on: &[&str], restart_on: &[&str]) -> Value {
    json!({
        "kind": "systemd_unit", "id": id, "depends_on": depends_on, "name": name,
        "content": format!("[Service]\nExecStart=/usr/local/lib/netagent/netcore run -c /etc/netagent/core.json\n# {id}\n"),
        "enabled": true, "state": "started", "restart_on": restart_on,
    })
}

/// A zip archive with the given (name, content) members.
pub fn zip_archive(members: &[(&str, &[u8])]) -> Vec<u8> {
    let mut buf = io::Cursor::new(Vec::new());
    {
        let mut writer = zip::ZipWriter::new(&mut buf);
        let options = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);
        for (name, content) in members {
            writer.start_file(*name, options).unwrap();
            writer.write_all(content).unwrap();
        }
        writer.finish().unwrap();
    }
    buf.into_inner()
}

pub fn tar_gz(members: &[(&str, &[u8])]) -> Vec<u8> {
    let encoder = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
    let mut builder = tar::Builder::new(encoder);
    for (name, content) in members {
        let mut header = tar::Header::new_gnu();
        header.set_size(content.len() as u64);
        header.set_mode(0o755);
        header.set_cksum();
        builder.append_data(&mut header, name, *content).unwrap();
    }
    builder.into_inner().unwrap().finish().unwrap()
}

// ---- Mock HTTP service ------------------------------------------------------------

#[derive(Clone, Debug)]
pub struct Recorded {
    pub method: String,
    pub path: String,
    pub authorization: Option<String>,
    pub body: Vec<u8>,
}

impl Recorded {
    pub fn json(&self) -> Value {
        serde_json::from_slice(&self.body).unwrap()
    }
}

type Handler = dyn Fn(&Recorded) -> (u16, Vec<u8>) + Send + Sync;

/// A tiny HTTP/1.1 server (one request per connection) on 127.0.0.1.
pub struct MockServer {
    pub url: String,
    pub requests: Arc<Mutex<Vec<Recorded>>>,
}

impl MockServer {
    pub fn start(handler: Box<Handler>) -> MockServer {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let requests = Arc::new(Mutex::new(Vec::new()));
        let recorded = Arc::clone(&requests);
        let handler: Arc<Handler> = Arc::from(handler);
        std::thread::spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { continue };
                let mut reader = BufReader::new(stream.try_clone().unwrap());
                let mut line = String::new();
                if reader.read_line(&mut line).unwrap_or(0) == 0 {
                    continue;
                }
                let mut parts = line.split_whitespace();
                let method = parts.next().unwrap_or("").to_string();
                let path = parts.next().unwrap_or("").to_string();
                let mut length = 0usize;
                let mut authorization = None;
                loop {
                    let mut header = String::new();
                    reader.read_line(&mut header).unwrap();
                    let header = header.trim_end();
                    if header.is_empty() {
                        break;
                    }
                    let (name, value) = header.split_once(':').unwrap();
                    match name.to_ascii_lowercase().as_str() {
                        "content-length" => length = value.trim().parse().unwrap(),
                        "authorization" => authorization = Some(value.trim().to_string()),
                        _ => {}
                    }
                }
                let mut body = vec![0; length];
                reader.read_exact(&mut body).unwrap();
                let request = Recorded {
                    method,
                    path,
                    authorization,
                    body,
                };
                let (status, payload) = handler(&request);
                recorded.lock().unwrap().push(request);
                let head = format!(
                    "HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                    payload.len()
                );
                let _ = stream.write_all(head.as_bytes());
                let _ = stream.write_all(&payload);
            }
        });
        MockServer { url, requests }
    }

    pub fn requests(&self) -> Vec<Recorded> {
        self.requests.lock().unwrap().clone()
    }
}
