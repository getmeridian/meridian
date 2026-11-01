//! Capability evidence follows the live core, including restarts outside apply.

use super::*;
use meridian_agent::system::Exec;

fn guarded_resources() -> Vec<Value> {
    let mut resources = supported();
    resources[1]["config"]["api"] =
        json!({"services": ["HandlerService", "StatsService", "RoutingService"]});
    resources[1]["config"]["outbounds"] = json!([
        {"tag": "direct", "protocol": "freedom"},
        {"tag": "block", "protocol": "blackhole"},
    ]);
    resources[1]["config"]["routing"] = json!({"rules": [
        {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
        {"type": "field", "network": "tcp,udp", "outboundTag": "direct"},
    ]});
    resources
}

fn evidence_after_restart(path: &str) {
    let pki = TestPki::new();
    let service = service(&pki);
    service.state.lock().unwrap().capabilities.extend(KINDS);
    service
        .state
        .lock()
        .unwrap()
        .capabilities
        .push("user_flow_blocking");
    let before = guarded_resources();
    service.push(document(1, 1, &before, &pki));
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    let mut after = before.clone();
    after[2] = vless_users(&[]);
    *exec.routing_down.lock().unwrap() = true;
    service.push(document(1, 2, &after, &pki));
    agent.poll_once().unwrap();
    assert_eq!(
        serde_json::to_value(agent.build_report()).unwrap()["user_flow_blocking"]["status"],
        "pending"
    );
    if path == "systemd" {
        exec.run("systemctl", &["restart", "netcore.service"], &[])
            .unwrap();
    } else {
        let fields = if path == "fallback" {
            *exec.api_down.lock().unwrap() = true;
            json!({"kind": "reset_inbound", "inbound_tag": "vless-reality"})
        } else {
            json!({"kind": "restart_unit", "resource_id": "unit:netcore"})
        };
        push(&service, &command(&pki, fields, 1, now_unix() + 300));
        agent.poll_once().unwrap();
        assert_eq!(result(&service)["status"], "succeeded");
        *exec.api_down.lock().unwrap() = false;
    }
    exec.clear();
    let report = serde_json::to_value(agent.build_report()).unwrap();
    assert_eq!(
        report["user_flow_blocking"]["status"], "ready",
        "{path}: {report}"
    );
    assert_eq!(report["user_flow_blocking"]["document"], report["applied"]);
    assert!(
        exec.calls()
            .iter()
            .all(|call| !call.contains(" api adrules "))
    );
    assert!(
        exec.routing.lock().unwrap()["rules"]
            .as_array()
            .unwrap()
            .iter()
            .all(|rule| !rule["ruleTag"]
                .as_str()
                .unwrap_or("")
                .starts_with("meridian-revoked-"))
    );
}

#[test]
fn core_evidence_recovers_after_a_signed_restart() {
    evidence_after_restart("command");
}

#[test]
fn core_evidence_recovers_after_a_reset_fallback() {
    evidence_after_restart("fallback");
}

#[test]
fn core_evidence_recovers_after_a_systemd_restart() {
    evidence_after_restart("systemd");
}

#[test]
fn core_evidence_checks_live_rules_without_repairing_them_during_report_build() {
    let pki = TestPki::new();
    let service = service(&pki);
    service
        .state
        .lock()
        .unwrap()
        .capabilities
        .push("user_flow_blocking");
    let before = guarded_resources();
    service.push(document(1, 1, &before, &pki));
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    let mut after = before.clone();
    after[2] = vless_users(&[]);
    service.push(document(1, 2, &after, &pki));
    agent.poll_once().unwrap();
    let journal = node.state.path().join("flow-revocations.json");
    let saved = std::fs::read(&journal).unwrap();
    *exec.routing.lock().unwrap() = before[1]["config"]["routing"].clone();
    exec.clear();
    let report = serde_json::to_value(agent.build_report()).unwrap();
    assert_eq!(report["user_flow_blocking"]["status"], "pending");
    assert!(
        exec.calls()
            .iter()
            .all(|call| !call.contains(" api adrules "))
    );
    assert_eq!(std::fs::read(journal).unwrap(), saved);
}
