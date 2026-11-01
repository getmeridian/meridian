//! Enrollment, `hello`, and renewal: how the agent proves who it is.
//!
//! `enroll` trades the bootstrap's one-time token and a CSR for a node
//! certificate; `hello` signs a `NodeProof` with the node key and opens a
//! session whose bearer token the client then sends on session endpoints;
//! `renew` certifies a freshly generated key. Every certificate the service
//! returns is checked against the pinned CA before it is stored or trusted.

use std::time::Duration;

use serde::de::DeserializeOwned;
use serde_json::{Value, json};

use crate::bootstrap::{Bootstrap, is_opaque_token};
use crate::fetch::{Client, HttpError};
use crate::identity::{
    Ca, Identity, ServiceCert, check_node_certificate, csr_pem, generate_key, random_token,
};
use crate::model::{
    EnrollRequest, EnrollResponse, HelloResponse, NodeStateRef, PROTOCOL, RenewRequest,
    RenewResponse,
};
use crate::timeutil::{format_unix, parse_rfc3339};

/// Everything this agent can do; the service answers with the subset it relies on.
pub const CAPABILITIES: &[&str] = &[
    "long_poll",
    "files",
    "systemd_units",
    "xray_config",
    "xray_users",
    "hysteria2_users",
    "user_flow_blocking",
    "binary_pins",
    "traffic_stats",
    "blob_fetch",
    "reconcile",
    "report",
    "reset_inbound",
    "restart_unit",
    "renew_credential",
];
/// Say hello again this long before the session expires.
const SESSION_MARGIN_SECONDS: u64 = 30;

/// The node's architecture as the contract names it.
pub fn arch() -> &'static str {
    match std::env::consts::ARCH {
        "aarch64" => "arm64",
        _ => "amd64",
    }
}

/// An open session. Times are on the agent's clock, shifted by the service's own offset,
/// so a node whose clock drifts still renews and re-hellos on time.
#[derive(Clone, Debug)]
pub struct Session {
    pub expires_at: u64,
    pub renew_after: u64,
    pub max_wait_seconds: u64,
    pub report_interval: Duration,
    pub stats_interval: Duration,
    pub capabilities: Vec<String>,
    pub fleet_epoch: u64,
    pub server_offset: i64,
}

impl Session {
    pub fn fresh(&self, now: u64) -> bool {
        now + SESSION_MARGIN_SECONDS < self.expires_at
    }

    pub fn uses(&self, capability: &str) -> bool {
        self.capabilities.iter().any(|c| c == capability)
    }

    pub fn server_now(&self, now: u64) -> u64 {
        now.saturating_add_signed(self.server_offset)
    }
}

fn protocol_error(message: impl Into<String>) -> HttpError {
    HttpError::Protocol(message.into())
}

fn typed<T: DeserializeOwned>(body: Value, expected: &str) -> Result<T, HttpError> {
    let kind = body.get("type").and_then(Value::as_str).unwrap_or_default();
    if kind != expected {
        return Err(protocol_error(format!("expected {expected}, got {kind:?}")));
    }
    serde_json::from_value(body).map_err(|err| protocol_error(err.to_string()))
}

/// Enroll with the bootstrap's token: a fresh key, a CSR, and checked certificates back.
pub fn enroll(client: &Client, bootstrap: &Bootstrap, now: u64) -> Result<Identity, HttpError> {
    let token = bootstrap
        .enrollment_token
        .clone()
        .ok_or_else(|| protocol_error("the bootstrap holds no enrollment token"))?;
    let key = generate_key().map_err(protocol_error)?;
    let request = EnrollRequest {
        kind: "enroll",
        protocol: PROTOCOL,
        fleet_id: bootstrap.fleet_id.clone(),
        node_id: bootstrap.node_id.clone(),
        enrollment_token: token,
        csr_pem: csr_pem(&key, &bootstrap.node_id).map_err(protocol_error)?,
        agent_version: crate::VERSION,
        arch: arch(),
    };
    let response: EnrollResponse = typed(client.open("enroll", &request)?, "enrolled")?;
    if response.protocol != PROTOCOL
        || response.fleet_id != bootstrap.fleet_id
        || response.node_id != bootstrap.node_id
    {
        return Err(protocol_error(
            "the enrollment answer names another fleet or node",
        ));
    }
    let ca =
        Ca::pinned(&response.ca_certificate_pem, &bootstrap.ca_sha256).map_err(protocol_error)?;
    check_node_certificate(
        &response.certificate_pem,
        &ca,
        &bootstrap.fleet_id,
        &bootstrap.node_id,
        &key.verifying_key(),
    )
    .map_err(protocol_error)?;
    let service = ServiceCert::check(
        &response.service_certificate_pem,
        &ca,
        &bootstrap.fleet_id,
        now,
    )
    .map_err(protocol_error)?;
    Ok(Identity {
        key,
        certificate_pem: response.certificate_pem,
        ca,
        service,
    })
}

/// The signed hello body; `issued_at` and the nonce make every one unique.
pub fn hello_body(
    bootstrap: &Bootstrap,
    identity: &Identity,
    highest_epoch: Option<u64>,
    applied: Option<&NodeStateRef>,
    now: u64,
) -> Result<Value, HttpError> {
    let mut body = json!({
        "type": "hello",
        "protocols": [PROTOCOL],
        "fleet_id": bootstrap.fleet_id,
        "node_id": bootstrap.node_id,
        "agent_version": crate::VERSION,
        "arch": arch(),
        "capabilities": CAPABILITIES,
        "highest_fleet_epoch": highest_epoch,
        "applied": applied,
        "proof": {
            "certificate_pem": identity.certificate_pem,
            "issued_at": format_unix(now),
            "nonce": random_token().map_err(protocol_error)?,
        },
    });
    body["proof"]["signature"] = json!(identity.sign_proof(&body).map_err(protocol_error)?);
    Ok(body)
}

/// Open a session; returns it and the service certificate the service presented (checked).
pub fn hello(
    client: &Client,
    bootstrap: &Bootstrap,
    identity: &Identity,
    highest_epoch: Option<u64>,
    applied: Option<&NodeStateRef>,
    now: u64,
) -> Result<(Session, ServiceCert), HttpError> {
    let body = hello_body(bootstrap, identity, highest_epoch, applied, now)?;
    let response: HelloResponse = typed(client.open("hello", &body)?, "welcome")?;
    if response.protocol != PROTOCOL {
        return Err(protocol_error("the service chose another protocol"));
    }
    if !is_opaque_token(&response.session_token) {
        return Err(protocol_error("the session token is malformed"));
    }
    let service = ServiceCert::check(
        &response.service_certificate_pem,
        &identity.ca,
        &bootstrap.fleet_id,
        now,
    )
    .map_err(protocol_error)?;
    let at = |text: &str| parse_rfc3339(text).ok_or_else(|| protocol_error("bad timestamp"));
    let server_time = at(&response.server_time)?;
    if response
        .capabilities
        .iter()
        .any(|capability| !CAPABILITIES.contains(&capability.as_str()))
    {
        return Err(protocol_error(
            "the service accepted a capability this agent did not offer",
        ));
    }
    let on_our_clock = |stamp: u64| now.saturating_add(stamp.saturating_sub(server_time));
    let session = Session {
        expires_at: on_our_clock(at(&response.session_expires_at)?),
        renew_after: on_our_clock(at(&response.renew_after)?),
        max_wait_seconds: response.poll.max_wait_seconds.clamp(1, 120),
        report_interval: Duration::from_secs(response.poll.report_interval_seconds.clamp(10, 3600)),
        stats_interval: Duration::from_secs(response.poll.stats_interval_seconds.clamp(10, 3600)),
        capabilities: response.capabilities,
        fleet_epoch: response.fleet_epoch,
        server_offset: server_time as i64 - now as i64,
    };
    client.set_session(Some(response.session_token));
    Ok((session, service))
}

/// Certify a freshly generated key over the session; the caller stores the result.
pub fn renew(
    client: &Client,
    bootstrap: &Bootstrap,
    identity: &Identity,
) -> Result<Identity, HttpError> {
    let key = generate_key().map_err(protocol_error)?;
    let request = RenewRequest {
        kind: "renew",
        csr_pem: csr_pem(&key, &bootstrap.node_id).map_err(protocol_error)?,
    };
    let response: RenewResponse = typed(client.call("renew", &request)?, "renewed")?;
    check_node_certificate(
        &response.certificate_pem,
        &identity.ca,
        &bootstrap.fleet_id,
        &bootstrap.node_id,
        &key.verifying_key(),
    )
    .map_err(protocol_error)?;
    Ok(Identity {
        key,
        certificate_pem: response.certificate_pem,
        ca: identity.ca.clone(),
        service: identity.service.clone(),
    })
}
