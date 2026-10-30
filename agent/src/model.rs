//! Typed `meridian.agent/v1` bodies and resources the agent speaks.
//!
//! Desired state is verified as a `serde_json::Value` first (see `verify`);
//! these structs are filled only afterwards. Every outgoing field is always
//! serialized, `null` included.

use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const PROTOCOL: &str = "meridian.agent/v1";

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct NodeStateRef {
    pub fleet_epoch: u64,
    pub revision: u64,
    pub sha256: String,
}

#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct ServiceSignature {
    pub key_id: String,
    pub signature: String,
}

/// The typed envelope of a desired-state document; resources stay raw JSON.
#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct DesiredDocument {
    pub protocol: String,
    pub fleet_id: String,
    pub node_id: String,
    pub fleet_epoch: u64,
    pub revision: u64,
    pub sha256: String,
    pub issued_at: String,
    pub resources: Vec<Value>,
    pub signature: ServiceSignature,
}

#[derive(Serialize, Debug)]
pub struct PollRequest {
    #[serde(rename = "type")]
    pub kind: &'static str,
    pub have: Option<NodeStateRef>,
    pub wait_seconds: u64,
}

#[derive(Deserialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct PollResponse {
    #[serde(rename = "type")]
    pub kind: String,
    pub fleet_epoch: u64,
    pub desired: Option<Value>,
    pub commands: Vec<Value>,
    pub poll_after_seconds: u64,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
pub struct ResourceError {
    pub code: String,
    pub message: String,
    pub retryable: bool,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
pub struct ResourceObservation {
    pub id: String,
    pub kind: String,
    pub status: String,
    pub applied_sha256: Option<String>,
    pub error: Option<ResourceError>,
    pub changed_at: String,
}

#[derive(Serialize, Clone, Debug)]
pub struct HealthCheck {
    pub name: String,
    pub status: &'static str,
    pub message: String,
}

#[derive(Serialize, Clone, Debug)]
pub struct NodeHealth {
    pub status: &'static str,
    pub checks: Vec<HealthCheck>,
    pub uptime_seconds: u64,
    pub load1_milli: u64,
    pub memory_available_bytes: u64,
    pub disk_free_bytes: u64,
}

#[derive(Serialize, Clone, Debug)]
pub struct NodeVersions {
    pub agent: String,
    pub xray: Option<String>,
    pub realm: Option<String>,
    pub nginx: Option<String>,
    pub os_id: String,
    pub os_version: String,
    pub kernel: String,
}

#[derive(Serialize, Clone, Debug)]
pub struct ObservedStateReport {
    #[serde(rename = "type")]
    pub kind: &'static str,
    pub fleet_id: String,
    pub node_id: String,
    pub applied: Option<NodeStateRef>,
    pub target: Option<NodeStateRef>,
    pub resources: Vec<ResourceObservation>,
    pub health: NodeHealth,
    pub versions: NodeVersions,
    pub observed_at: String,
}

#[derive(Deserialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct ReportAck {
    #[serde(rename = "type")]
    pub kind: String,
    pub applied_revision: Option<u64>,
}

#[derive(Deserialize, Debug, Clone)]
pub struct AgentError {
    pub code: String,
    pub message: String,
    pub retryable: bool,
    pub retry_after_seconds: Option<u64>,
    pub server_time: String,
}

#[derive(Deserialize, Debug, Clone)]
pub struct AgentErrorResponse {
    #[serde(rename = "type")]
    pub kind: String,
    pub protocol: String,
    pub error: AgentError,
}

// ---- Resources --------------------------------------------------------------

/// The fields every resource carries, read before the kind is known.
#[derive(Deserialize, Clone, Debug)]
pub struct ResourceHeader {
    pub id: String,
    pub kind: String,
    pub depends_on: Vec<String>,
}

#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct FileSpec {
    pub id: String,
    pub kind: String,
    pub depends_on: Vec<String>,
    pub path: String,
    pub mode: String,
    pub owner: String,
    pub group: String,
    pub size: u64,
    pub sha256: String,
    pub content_b64: Option<String>,
    pub sensitive: bool,
}

#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct UnitSpec {
    pub id: String,
    pub kind: String,
    pub depends_on: Vec<String>,
    pub name: String,
    pub content: String,
    pub enabled: bool,
    pub state: String,
    pub restart_on: Vec<String>,
}

#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct XrayConfigSpec {
    pub id: String,
    pub kind: String,
    pub depends_on: Vec<String>,
    pub path: String,
    pub config: Value,
    pub api_address: String,
}

#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct VlessUser {
    pub user_id: String,
    pub uuid: String,
    pub flow: String,
}

#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct XrayUsersSpec {
    pub id: String,
    pub kind: String,
    pub depends_on: Vec<String>,
    pub inbound_tag: String,
    pub users: Vec<VlessUser>,
}

#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct Hysteria2User {
    pub user_id: String,
    pub auth: String,
}

#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct Hysteria2UsersSpec {
    pub id: String,
    pub kind: String,
    pub depends_on: Vec<String>,
    pub inbound_tag: String,
    pub users: Vec<Hysteria2User>,
}

#[derive(Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct BinaryPinSpec {
    pub id: String,
    pub kind: String,
    pub depends_on: Vec<String>,
    pub name: String,
    pub version: String,
    pub sources: Vec<String>,
    pub size: u64,
    pub sha256: String,
    pub archive: String,
    pub member: Option<String>,
    pub install_path: String,
    pub mode: String,
}

#[derive(Clone, Debug)]
pub enum Spec {
    File(FileSpec),
    Unit(UnitSpec),
    XrayConfig(XrayConfigSpec),
    XrayUsers(XrayUsersSpec),
    Hysteria2Users(Hysteria2UsersSpec),
    BinaryPin(BinaryPinSpec),
    /// A contract kind this skeleton does not implement.
    Unsupported,
    /// A supported kind whose fields do not parse or validate.
    Invalid(String),
}

/// One resource of a verified document.
#[derive(Clone, Debug)]
pub struct Resource {
    pub id: String,
    pub kind: String,
    pub depends_on: Vec<String>,
    /// The resource object as received.
    pub raw: Value,
    /// Protocol hash of `raw` (the observation's `applied_sha256`).
    pub hash: String,
    pub spec: Spec,
}

impl Resource {
    pub fn parse(raw: &Value) -> Result<Resource, String> {
        let header: ResourceHeader =
            serde_json::from_value(raw.clone()).map_err(|err| format!("resource header: {err}"))?;
        let hash = crate::jcs::canonical_sha256(raw)?;
        let spec = parse_spec(&header.kind, raw);
        Ok(Resource {
            id: header.id,
            kind: header.kind,
            depends_on: header.depends_on,
            raw: raw.clone(),
            hash,
            spec,
        })
    }
}

fn typed<T: serde::de::DeserializeOwned>(raw: &Value) -> Result<T, String> {
    serde_json::from_value(raw.clone()).map_err(|err| err.to_string())
}

fn parse_spec(kind: &str, raw: &Value) -> Spec {
    let parsed = match kind {
        "file" => typed::<FileSpec>(raw).and_then(|s| {
            check_mode(&s.mode)?;
            check_hash(&s.sha256)?;
            Ok(Spec::File(s))
        }),
        "systemd_unit" => typed::<UnitSpec>(raw).and_then(|s| {
            if s.state != "started" && s.state != "stopped" {
                return Err(format!("unknown unit state {:?}", s.state));
            }
            if !s.restart_on.iter().all(|id| s.depends_on.contains(id)) {
                return Err("restart_on must be a subset of depends_on".into());
            }
            Ok(Spec::Unit(s))
        }),
        "xray_config" => typed::<XrayConfigSpec>(raw).and_then(|s| {
            if !s.config.is_object() {
                return Err("config must be an object".into());
            }
            check_loopback(&s.api_address)?;
            Ok(Spec::XrayConfig(s))
        }),
        "xray_users" => typed::<XrayUsersSpec>(raw).and_then(|s| {
            check_tag(&s.inbound_tag)?;
            s.users.iter().try_for_each(|u| check_user_id(&u.user_id))?;
            if s.users
                .iter()
                .any(|u| !u.flow.is_empty() && u.flow != "xtls-rprx-vision")
            {
                return Err("unknown VLESS flow".into());
            }
            Ok(Spec::XrayUsers(s))
        }),
        "hysteria2_users" => typed::<Hysteria2UsersSpec>(raw).and_then(|s| {
            check_tag(&s.inbound_tag)?;
            s.users.iter().try_for_each(|u| check_user_id(&u.user_id))?;
            Ok(Spec::Hysteria2Users(s))
        }),
        "binary_pin" => typed::<BinaryPinSpec>(raw).and_then(|s| {
            check_mode(&s.mode)?;
            check_hash(&s.sha256)?;
            if !["xray", "realm", "geoip", "geosite"].contains(&s.name.as_str()) {
                return Err(format!("unknown binary name {:?}", s.name));
            }
            if !["none", "zip", "tar.gz"].contains(&s.archive.as_str()) {
                return Err(format!("unknown archive {:?}", s.archive));
            }
            if s.member.is_none() != (s.archive == "none") {
                return Err("member is required for an archive and forbidden otherwise".into());
            }
            if s.sources.is_empty() {
                return Err("no sources".into());
            }
            Ok(Spec::BinaryPin(s))
        }),
        _ => return Spec::Unsupported,
    };
    parsed.unwrap_or_else(Spec::Invalid)
}

/// Parse a four-digit octal mode such as `0644`.
pub fn parse_mode(mode: &str) -> Result<u32, String> {
    check_mode(mode)?;
    u32::from_str_radix(mode, 8).map_err(|err| err.to_string())
}

fn check_mode(mode: &str) -> Result<(), String> {
    if mode.len() == 4 && mode.bytes().all(|b| (b'0'..=b'7').contains(&b)) {
        Ok(())
    } else {
        Err(format!("invalid mode {mode:?}"))
    }
}

/// `InboundTag` in `meridian.core.agent_types`: it names a cache file and an `xray api` argument.
pub(crate) fn check_tag(tag: &str) -> Result<(), String> {
    let mut bytes = tag.bytes();
    let first = bytes.next().is_some_and(|b| b.is_ascii_alphanumeric());
    if first && tag.len() <= 64 && bytes.all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b)) {
        Ok(())
    } else {
        Err(format!("invalid inbound tag {tag:?}"))
    }
}

/// `UserId` in `meridian.core.agent_types`.
fn check_user_id(user_id: &str) -> Result<(), String> {
    let allowed = |b: u8| b.is_ascii_lowercase() || b.is_ascii_digit();
    let mut bytes = user_id.bytes();
    let first = bytes.next().is_some_and(allowed);
    if first && user_id.len() <= 64 && bytes.all(|b| allowed(b) || b"_-".contains(&b)) {
        Ok(())
    } else {
        Err(format!("invalid user id {user_id:?}"))
    }
}

/// The Xray API speaks plaintext gRPC, so it may only listen on loopback.
pub(crate) fn check_loopback(address: &str) -> Result<(), String> {
    let port = address
        .strip_prefix("127.0.0.1:")
        .and_then(|p| p.parse::<u16>().ok());
    match port {
        Some(port) if port > 0 => Ok(()),
        _ => Err(format!("api_address {address:?} is not 127.0.0.1:PORT")),
    }
}

fn check_hash(hash: &str) -> Result<(), String> {
    if crate::jcs::is_sha256_hex(hash) {
        Ok(())
    } else {
        Err("sha256 is not lowercase hex".into())
    }
}

// ---- Enrollment, sessions, and stats ---------------------------------------------

#[derive(Serialize, Debug)]
pub struct EnrollRequest {
    #[serde(rename = "type")]
    pub kind: &'static str,
    pub protocol: &'static str,
    pub fleet_id: String,
    pub node_id: String,
    pub enrollment_token: String,
    pub csr_pem: String,
    pub agent_version: &'static str,
    pub arch: &'static str,
}

#[derive(Deserialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct EnrollResponse {
    #[serde(rename = "type")]
    pub kind: String,
    pub protocol: String,
    pub fleet_id: String,
    pub node_id: String,
    pub fleet_epoch: u64,
    pub certificate_pem: String,
    pub certificate_expires_at: String,
    pub ca_certificate_pem: String,
    pub service_certificate_pem: String,
}

#[derive(Deserialize, Debug, Clone)]
#[serde(deny_unknown_fields)]
pub struct PollSettings {
    pub max_wait_seconds: u64,
    pub report_interval_seconds: u64,
    pub stats_interval_seconds: u64,
}

#[derive(Deserialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct HelloResponse {
    #[serde(rename = "type")]
    pub kind: String,
    pub protocol: String,
    pub fleet_epoch: u64,
    pub capabilities: Vec<String>,
    pub session_token: String,
    pub session_expires_at: String,
    pub server_time: String,
    pub poll: PollSettings,
    pub renew_after: String,
    pub service_certificate_pem: String,
}

#[derive(Serialize, Debug)]
pub struct RenewRequest {
    #[serde(rename = "type")]
    pub kind: &'static str,
    pub csr_pem: String,
}

#[derive(Deserialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct RenewResponse {
    #[serde(rename = "type")]
    pub kind: String,
    pub certificate_pem: String,
    pub certificate_expires_at: String,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct UserTraffic {
    pub user_id: String,
    pub uplink_bytes: u64,
    pub downlink_bytes: u64,
}

#[derive(Serialize, Clone, Debug)]
pub struct StatsReport {
    #[serde(rename = "type")]
    pub kind: &'static str,
    pub counter_epoch: String,
    pub seq: u64,
    pub collected_at: String,
    pub users: Vec<UserTraffic>,
}

#[derive(Deserialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct StatsAck {
    #[serde(rename = "type")]
    pub kind: String,
    pub counter_epoch: String,
    pub acked_seq: u64,
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn users(tag: &str, user_id: &str) -> Value {
        json!({
            "id": "users", "kind": "xray_users", "depends_on": [], "inbound_tag": tag,
            "users": [{"user_id": user_id, "uuid": "00000000-0000-4000-8000-000000000000", "flow": ""}],
        })
    }

    #[test]
    fn names_that_reach_paths_or_xray_arguments_are_refused() {
        assert!(matches!(
            Resource::parse(&users("vless-reality", "u1a"))
                .unwrap()
                .spec,
            Spec::XrayUsers(_)
        ));
        for (tag, user_id) in [
            ("x/../../etc/cron.d/pwn", "u1a"),
            ("-server=198.51.100.9:80", "u1a"),
            ("vless-reality", "--server=198.51.100.9:80"),
            ("vless-reality", "U1A"),
        ] {
            let spec = Resource::parse(&users(tag, user_id)).unwrap().spec;
            assert!(matches!(spec, Spec::Invalid(_)), "{tag} / {user_id}");
        }
    }

    #[test]
    fn the_xray_api_listens_only_on_loopback() {
        for (address, valid) in [
            ("127.0.0.1:10085", true),
            ("198.51.100.9:10085", false),
            ("127.0.0.1:0", false),
            ("127.0.0.1:", false),
        ] {
            let raw = json!({
                "id": "core", "kind": "xray_config", "depends_on": [], "path": "/etc/netagent/core.json",
                "config": {}, "api_address": address,
            });
            let spec = Resource::parse(&raw).unwrap().spec;
            assert_eq!(matches!(spec, Spec::XrayConfig(_)), valid, "{address}");
        }
    }
}
