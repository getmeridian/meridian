//! Commands run only after wire verification, and a lost acknowledgement never repeats a restart.

#[path = "command_sessions.rs"]
mod sessions;

use super::*;
use base64::Engine as _;
use base64::engine::general_purpose::STANDARD;
use ed25519_dalek::Signer;
use meridian_agent::timeutil::{format_unix, now_unix};

const ID: &str = "1b6f3c1e-8f6a-4d2b-9c41-7a5e2d9f0c13";
const KINDS: &[&str] = &[
    "reconcile",
    "report",
    "reset_inbound",
    "restart_unit",
    "renew_credential",
];

fn command(pki: &TestPki, fields: Value, epoch: u64, expires: u64) -> Value {
    let mut body = fields.as_object().unwrap().clone();
    body.insert("command_id".into(), json!(ID));
    body.insert("fleet_epoch".into(), json!(epoch));
    body.insert(
        "issued_at".into(),
        json!(format_unix(now_unix().saturating_sub(5))),
    );
    body.insert("expires_at".into(), json!(format_unix(expires)));
    let mut signed = b"meridian.agent/v1 command\n".to_vec();
    signed.extend(jcs::canonical(&Value::Object(body.clone())).unwrap());
    let signature = STANDARD.encode(pki.service_key.sign(&signed).to_bytes());
    body.insert(
        "signature".into(),
        json!({"key_id": pki.key_id(), "signature": signature}),
    );
    Value::Object(body)
}

fn ready_service(pki: &TestPki) -> FakeService {
    let service = service(pki);
    service.state.lock().unwrap().capabilities.extend(KINDS);
    service.push(document(1, 1, &supported(), pki));
    service
}

fn push(service: &FakeService, command: &Value) {
    service.state.lock().unwrap().commands.push(command.clone());
}

fn result(service: &FakeService) -> Value {
    service
        .requests_to("command-results")
        .last()
        .expect("the agent must answer the command")
        .json()
}

#[test]
fn a_signed_restart_uses_readiness_and_a_redelivery_after_restart_returns_its_result() {
    let pki = TestPki::new();
    let service = ready_service(&pki);
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    exec.clear();
    let restart = command(
        &pki,
        json!({"kind": "restart_unit", "resource_id": "unit:netcore"}),
        1,
        now_unix() + 300,
    );
    push(&service, &restart);
    agent.poll_once().unwrap();
    assert_eq!(exec.count("systemctl restart netcore.service"), 1);
    assert!(
        exec.calls()
            .iter()
            .any(|call| call.contains("statsquery") && !call.contains("-reset"))
    );
    assert_eq!(result(&service)["status"], "succeeded");
    assert_eq!(mode(&node.state.path().join("commands.json")), 0o600);
    let first = result(&service);
    drop(agent);
    exec.clear();
    let mut restarted = Agent::new(node.options(), &exec).unwrap();
    push(&service, &restart);
    restarted.poll_once().unwrap();
    assert_eq!(result(&service), first);
    assert_eq!(exec.count("systemctl restart netcore.service"), 0);
}

#[test]
fn tampering_unknown_keys_lower_epochs_expiry_and_unmanaged_targets_never_restart() {
    for defect in [
        "signature",
        "key",
        "epoch",
        "expiry",
        "extra",
        "extra-unit",
        "unknown-unit",
        "self",
        "unnegotiated",
        "float",
    ] {
        let pki = TestPki::new();
        let service = ready_service(&pki);
        let node = Node::new(&service.url(), &pki);
        let exec = FakeExec::default();
        let mut agent = Agent::new(node.options(), &exec).unwrap();
        agent.poll_once().unwrap();
        exec.clear();
        let mut fields = json!({"kind": "restart_unit", "resource_id": "unit:netcore"});
        if defect == "unknown-unit" {
            fields["resource_id"] = json!("unit:missing");
        }
        if defect == "self" {
            fields["resource_id"] = json!("unit:netagent");
        }
        if defect == "extra" {
            fields["secret"] = json!("must-never-run");
        }
        if defect == "extra-unit" {
            fields = json!({"kind": "reconcile", "unexpected": true});
        }
        let expiry = if defect == "expiry" {
            now_unix().saturating_sub(1)
        } else {
            now_unix() + 300
        };
        let mut value = command(&pki, fields, 1, expiry);
        if defect == "signature" {
            value["resource_id"] = json!("unit:other");
        }
        if defect == "key" {
            value["signature"]["key_id"] = json!("0".repeat(64));
        }
        if defect == "float" {
            value["fleet_epoch"] = json!(1.0);
        }
        if defect == "epoch" {
            agent.state.highest_epoch = Some(2);
        }
        if defect == "unnegotiated" {
            service
                .state
                .lock()
                .unwrap()
                .capabilities
                .retain(|cap| *cap != "restart_unit");
            service.state.lock().unwrap().sessions.clear();
        }
        push(&service, &value);
        agent.poll_once().unwrap();
        let observed = result(&service);
        assert_eq!(
            observed["status"],
            if defect == "expiry" {
                "expired"
            } else {
                "rejected"
            },
            "{defect}: {observed}"
        );
        assert_eq!(
            exec.count("systemctl restart netcore.service"),
            0,
            "{defect}"
        );
        assert_eq!(
            exec.count("systemctl restart netagent.service"),
            0,
            "{defect}"
        );
    }
}

#[test]
fn reset_rebuilds_only_the_composed_inbound_and_reports_a_core_fallback() {
    for api_fails in [false, true] {
        let pki = TestPki::new();
        let service = ready_service(&pki);
        let node = Node::new(&service.url(), &pki);
        let exec = FakeExec::default();
        let mut agent = Agent::new(node.options(), &exec).unwrap();
        agent.poll_once().unwrap();
        exec.clear();
        *exec.api_down.lock().unwrap() = api_fails;
        push(
            &service,
            &command(
                &pki,
                json!({"kind": "reset_inbound", "inbound_tag": "vless-reality"}),
                1,
                now_unix() + 300,
            ),
        );
        agent.poll_once().unwrap();
        let observed = result(&service);
        assert_eq!(observed["status"], "succeeded", "{observed}");
        assert_eq!(
            observed["effect"],
            if api_fails {
                "core_restart"
            } else {
                "inbound_reset"
            }
        );
        assert_eq!(
            exec.count("systemctl restart netcore.service"),
            usize::from(api_fails)
        );
        if !api_fails {
            let rebuilt = exec.rebuilt.lock().unwrap();
            assert_eq!(rebuilt.len(), 1);
            assert_eq!(
                rebuilt[0]["inbounds"][0]["settings"]["clients"][0]["id"],
                UUID_A
            );
        }
        let error_text = observed.to_string();
        assert!(!error_text.contains(UUID_A));
        assert!(!error_text.contains("secret-canary"));
    }
}

#[test]
fn failed_readiness_is_a_failed_command_and_is_not_repeated() {
    let pki = TestPki::new();
    let service = ready_service(&pki);
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    exec.clear();
    exec.crashing
        .lock()
        .unwrap()
        .insert("netcore.service".into());
    let restart = command(
        &pki,
        json!({"kind": "restart_unit", "resource_id": "unit:netcore"}),
        1,
        now_unix() + 300,
    );
    push(&service, &restart);
    agent.poll_once().unwrap();
    assert_eq!(result(&service)["status"], "failed");
    assert_eq!(exec.count("systemctl restart netcore.service"), 1);
    push(&service, &restart);
    agent.poll_once().unwrap();
    assert_eq!(exec.count("systemctl restart netcore.service"), 1);
}

#[test]
fn report_reconcile_and_renew_run_their_existing_paths_now() {
    for fields in [
        json!({"kind": "report", "include_stats": true}),
        json!({"kind": "reconcile"}),
        json!({"kind": "renew_credential"}),
    ] {
        let pki = TestPki::new();
        let service = ready_service(&pki);
        let node = Node::new(&service.url(), &pki);
        let exec = FakeExec::default();
        let mut agent = Agent::new(node.options(), &exec).unwrap();
        agent.poll_once().unwrap();
        exec.clear();
        let reports = service.requests_to("report").len();
        push(
            &service,
            &command(&pki, fields.clone(), 1, now_unix() + 300),
        );
        agent.poll_once().unwrap();
        assert_eq!(result(&service)["status"], "succeeded");
        match fields["kind"].as_str().unwrap() {
            "report" => {
                assert_eq!(service.requests_to("report").len(), reports + 1);
                assert_eq!(service.requests_to("stats").len(), 1);
            }
            "reconcile" => {
                assert!(
                    exec.calls()
                        .iter()
                        .any(|call| call.starts_with("systemctl is-active"))
                );
                assert_eq!(service.requests_to("report").len(), reports + 1);
            }
            "renew_credential" => assert_eq!(service.requests_to("renew").len(), 1),
            _ => unreachable!(),
        }
    }
}

#[test]
fn a_lost_result_ack_is_retried_after_an_agent_restart_without_repeating_the_command() {
    let pki = TestPki::new();
    let service = ready_service(&pki);
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    exec.clear();
    service.state.lock().unwrap().result_refusals = 1;
    push(
        &service,
        &command(
            &pki,
            json!({"kind": "restart_unit", "resource_id": "unit:netcore"}),
            1,
            now_unix() + 300,
        ),
    );
    assert!(agent.poll_once().is_err());
    assert_eq!(exec.count("systemctl restart netcore.service"), 1);
    let original = result(&service);
    drop(agent);
    let mut restarted = Agent::new(node.options(), &exec).unwrap();
    restarted.poll_once().unwrap();
    assert_eq!(result(&service), original);
    assert_eq!(exec.count("systemctl restart netcore.service"), 1);
    assert_eq!(service.requests_to("command-results").len(), 2);
}

#[test]
fn removing_a_vless_user_leaves_open_sessions_until_its_signed_inbound_reset() {
    let pki = TestPki::new();
    let service = ready_service(&pki);
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    exec.live_sessions
        .lock()
        .unwrap()
        .insert("vless-reality".into(), ["u-a".into()].into());
    exec.live_sessions
        .lock()
        .unwrap()
        .insert("another-inbound".into(), ["u-b".into()].into());
    let mut removed = supported();
    removed[2] = vless_users(&[]);
    exec.clear();
    service.push(document(1, 2, &removed, &pki));
    agent.poll_once().unwrap();
    assert_eq!(exec.count("systemctl restart netcore.service"), 0);
    assert!(
        exec.live_sessions
            .lock()
            .unwrap()
            .contains_key("vless-reality")
    );
    push(
        &service,
        &command(
            &pki,
            json!({"kind": "reset_inbound", "inbound_tag": "vless-reality"}),
            1,
            now_unix() + 300,
        ),
    );
    agent.poll_once().unwrap();
    assert!(
        !exec
            .live_sessions
            .lock()
            .unwrap()
            .contains_key("vless-reality")
    );
    assert!(
        exec.live_sessions
            .lock()
            .unwrap()
            .contains_key("another-inbound")
    );
    let rebuilt = exec.rebuilt.lock().unwrap();
    assert!(
        rebuilt[0]["inbounds"][0]["settings"]["clients"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    assert_eq!(result(&service)["effect"], "inbound_reset");
}

#[test]
fn no_command_runs_when_its_result_cannot_be_saved() {
    let pki = TestPki::new();
    let service = ready_service(&pki);
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    exec.clear();
    std::fs::create_dir(node.state.path().join("commands.json")).unwrap();
    push(
        &service,
        &command(
            &pki,
            json!({"kind": "restart_unit", "resource_id": "unit:netcore"}),
            1,
            now_unix() + 300,
        ),
    );
    assert!(agent.poll_once().is_err());
    assert_eq!(exec.count("systemctl restart netcore.service"), 0);
    assert!(Agent::new(node.options(), &exec).is_err());
    assert!(self_check(&node.bootstrap(), node.state.path(), true).is_err());
}

#[test]
fn an_interrupted_outcome_is_reported_after_restart_without_repeating_the_action() {
    use meridian_agent::commands::{CommandResult, CommandState, CommandStatus, RememberedCommand};
    let pki = TestPki::new();
    let service = ready_service(&pki);
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    exec.clear();
    let expiry = now_unix() + 300;
    let restart = command(
        &pki,
        json!({"kind": "restart_unit", "resource_id": "unit:netcore"}),
        1,
        expiry,
    );
    let interrupted = CommandResult::failure(
        ID,
        CommandStatus::Failed,
        "internal",
        "The command was interrupted; inspect the node.",
        now_unix(),
    );
    CommandState {
        results: vec![RememberedCommand {
            digest: jcs::canonical_sha256(&restart).unwrap(),
            expires_at: expiry,
            result: interrupted,
            acknowledged: false,
        }],
    }
    .save(node.state.path())
    .unwrap();
    drop(agent);
    push(&service, &restart);
    let mut restarted = Agent::new(node.options(), &exec).unwrap();
    restarted.poll_once().unwrap();
    assert_eq!(result(&service)["status"], "failed");
    assert_eq!(exec.count("systemctl restart netcore.service"), 0);
}

#[test]
fn command_expiry_uses_the_negotiated_service_clock() {
    let pki = TestPki::new();
    let service = ready_service(&pki);
    service.state.lock().unwrap().clock_offset = -3600;
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    exec.clear();
    let server_now = now_unix() - 3600;
    let mut value = command(
        &pki,
        json!({"kind": "restart_unit", "resource_id": "unit:netcore"}),
        1,
        server_now + 300,
    );
    value["issued_at"] = json!(format_unix(server_now - 5));
    value.as_object_mut().unwrap().remove("signature");
    let mut signed = b"meridian.agent/v1 command\n".to_vec();
    signed.extend(jcs::canonical(&value).unwrap());
    value["signature"] = json!({"key_id": pki.key_id(), "signature": STANDARD.encode(pki.service_key.sign(&signed).to_bytes())});
    push(&service, &value);
    agent.poll_once().unwrap();
    assert_eq!(result(&service)["status"], "succeeded");
    assert_eq!(exec.count("systemctl restart netcore.service"), 1);
}

#[test]
fn a_reused_id_with_different_signed_content_is_rejected() {
    let pki = TestPki::new();
    let service = ready_service(&pki);
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    push(
        &service,
        &command(
            &pki,
            json!({"kind": "report", "include_stats": false}),
            1,
            now_unix() + 300,
        ),
    );
    agent.poll_once().unwrap();
    exec.clear();
    push(
        &service,
        &command(
            &pki,
            json!({"kind": "restart_unit", "resource_id": "unit:netcore"}),
            1,
            now_unix() + 300,
        ),
    );
    agent.poll_once().unwrap();
    assert_eq!(result(&service)["status"], "rejected");
    assert_eq!(exec.count("systemctl restart netcore.service"), 0);
}

#[test]
fn an_obsolete_cached_result_never_blocks_polling_for_new_desired_state() {
    let pki = TestPki::new();
    let service = ready_service(&pki);
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    exec.clear();
    service.state.lock().unwrap().result_refusals = 1;
    push(
        &service,
        &command(
            &pki,
            json!({"kind": "restart_unit", "resource_id": "unit:netcore"}),
            1,
            now_unix() + 300,
        ),
    );
    assert!(agent.poll_once().is_err());
    service.state.lock().unwrap().reject_results = true;
    service.push(document(1, 2, &supported(), &pki));
    assert_eq!(
        agent.poll_once().unwrap(),
        PollOutcome::Recorded(Duration::ZERO)
    );
    assert_eq!(exec.count("systemctl restart netcore.service"), 1);
}

#[test]
fn signed_impossible_dates_and_excess_fraction_digits_are_rejected() {
    for (field, invalid) in [
        ("expires_at", "2026-11-31T12:00:00Z"),
        ("issued_at", "2026-02-31T12:00:00Z"),
        ("expires_at", "2026-11-01T12:00:00.0000000000Z"),
    ] {
        let pki = TestPki::new();
        let service = ready_service(&pki);
        let node = Node::new(&service.url(), &pki);
        let exec = FakeExec::default();
        let mut agent = Agent::new(node.options(), &exec).unwrap();
        agent.poll_once().unwrap();
        exec.clear();
        let mut value = command(
            &pki,
            json!({"kind": "restart_unit", "resource_id": "unit:netcore"}),
            1,
            now_unix() + 300,
        );
        value[field] = json!(invalid);
        value.as_object_mut().unwrap().remove("signature");
        let mut signed = b"meridian.agent/v1 command\n".to_vec();
        signed.extend(jcs::canonical(&value).unwrap());
        value["signature"] = json!({"key_id": pki.key_id(), "signature": STANDARD.encode(pki.service_key.sign(&signed).to_bytes())});
        push(&service, &value);
        agent.poll_once().unwrap();
        assert_eq!(result(&service)["status"], "rejected", "{field}: {invalid}");
        assert_eq!(exec.count("systemctl restart netcore.service"), 0);
    }
}
