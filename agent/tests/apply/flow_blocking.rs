//! Removing authentication must also block new streams on authenticated sessions.

use super::*;
use std::collections::BTreeSet;

fn guarded_document(vless: &[(&str, &str, &str)], hysteria: &[(&str, &str)]) -> Vec<Value> {
    let mut doc = full_document(vless);
    doc[5]["config"]["api"] =
        json!({"tag": "api", "services": ["HandlerService", "StatsService", "RoutingService"]});
    doc[5]["config"]["outbounds"] = json!([
        {"tag": "direct", "protocol": "freedom"},
        {"tag": "block", "protocol": "blackhole"},
    ]);
    doc[5]["config"]["routing"] = json!({"rules": [
        {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
        {"type": "field", "network": "tcp,udp", "outboundTag": "direct"},
    ]});
    doc[7] = hy2_users(hysteria);
    doc
}

#[test]
fn removing_a_person_blocks_their_new_streams_before_removing_passwords() {
    let mut host = Host::new(fetcher());
    host.apply(&guarded_document(
        &[("u-a", UUID_A, ""), ("u-b", UUID_B, "")],
        &[("u-a", UUID_A), ("u-b", UUID_B)],
    ));
    host.exec.clear();
    let pass = host.apply(&guarded_document(
        &[("u-b", UUID_B, "")],
        &[("u-b", UUID_B)],
    ));
    assert!(pass.converged, "{:?}", pass.observations);
    let calls = host.exec.calls();
    let block = calls.iter().position(|c| c.contains(" api adrules "));
    let remove = calls.iter().position(|c| c.contains(" api rmu ")).unwrap();
    assert!(
        block.is_some_and(|b| b < remove),
        "new streams still pass after rmu: {calls:?}"
    );
    assert!(
        calls
            .iter()
            .all(|c| !c.contains(" api rmi ") && !c.contains("systemctl restart"))
    );
}

#[test]
fn rotating_hysteria_credentials_preserves_other_peoples_sessions() {
    let mut host = Host::new(fetcher());
    host.apply(&guarded_document(&[], &[("u-a", UUID_A), ("u-b", UUID_B)]));
    host.exec
        .live_sessions
        .lock()
        .unwrap()
        .insert("hy2".into(), BTreeSet::from(["u-a".into(), "u-b".into()]));
    host.exec.clear();
    let pass = host.apply(&guarded_document(
        &[],
        &[
            ("u-a", "5f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00"),
            ("u-b", UUID_B),
        ],
    ));
    assert!(pass.converged, "{:?}", pass.observations);
    assert!(
        host.exec
            .live_sessions
            .lock()
            .unwrap()
            .get("hy2")
            .is_some_and(|sessions| sessions.contains("u-b")),
        "rotation reset another person's inbound"
    );
}

#[test]
fn a_failed_rule_add_still_removes_authentication_and_retries_without_a_restart() {
    let mut host = Host::new(fetcher());
    host.apply(&guarded_document(
        &[("u-a", UUID_A, ""), ("u-b", UUID_B, "")],
        &[("u-a", UUID_A), ("u-b", UUID_B)],
    ));
    *host.exec.routing_down.lock().unwrap() = true;
    host.exec.clear();
    let doc = guarded_document(&[("u-b", UUID_B, "")], &[("u-b", UUID_B)]);
    let pass = host.apply(&doc);
    assert!(pass.converged, "authentication removal must still apply");
    assert!(
        pass.flow_error
            .is_some_and(|error| error.retryable && !error.message.contains("secret-canary"))
    );
    assert_eq!(clients(&host, 1).as_array().unwrap().len(), 1);
    assert!(
        host.exec
            .calls()
            .iter()
            .any(|call| call.contains(" api rmu "))
    );
    assert!(
        !host
            .exec
            .calls()
            .iter()
            .any(|call| call.contains("systemctl restart"))
    );
    *host.exec.routing_down.lock().unwrap() = false;
    host.exec.clear();
    let repaired = host.apply(&doc);
    assert!(repaired.flow_error.is_none());
    assert!(
        host.exec
            .calls()
            .iter()
            .any(|call| call.contains(" api adrules "))
    );
    host.exec.clear();
    assert!(host.apply(&doc).flow_error.is_none());
    assert!(
        !host
            .exec
            .calls()
            .iter()
            .any(|call| call.contains(" api adrules "))
    );
}

#[test]
fn a_removal_installs_its_revocation_rules_only_once() {
    let mut host = Host::new(fetcher());
    host.apply(&guarded_document(
        &[("u-a", UUID_A, ""), ("u-b", UUID_B, "")],
        &[("u-a", UUID_A), ("u-b", UUID_B)],
    ));
    host.exec.clear();
    let doc = guarded_document(&[("u-b", UUID_B, "")], &[("u-b", UUID_B)]);
    let pass = host.apply(&doc);
    assert!(pass.converged && pass.flow_error.is_none(), "{pass:?}");
    assert_eq!(
        host.exec
            .calls()
            .iter()
            .filter(|call| call.contains(" api adrules "))
            .count(),
        1,
        "the end-of-apply check must keep the installed revocation rules",
    );
    host.exec.clear();
    assert!(host.apply(&doc).flow_error.is_none());
    assert!(
        !host
            .exec
            .calls()
            .iter()
            .any(|call| call.contains(" api adrules "))
    );
}

#[test]
fn later_revocations_keep_the_earlier_identity_blocked() {
    let mut host = Host::new(fetcher());
    let third = "5f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00";
    host.apply(&guarded_document(
        &[("u-a", UUID_A, ""), ("u-b", UUID_B, ""), ("u-c", third, "")],
        &[],
    ));
    host.apply(&guarded_document(
        &[("u-b", UUID_B, ""), ("u-c", third, "")],
        &[],
    ));
    let pass = host.apply(&guarded_document(&[("u-c", third, "")], &[]));
    assert!(pass.flow_error.is_none());
    let routing = host.exec.routing.lock().unwrap();
    for email in [
        meridian_agent::xray_identity::email("u-a", UUID_A),
        meridian_agent::xray_identity::email("u-b", UUID_B),
    ] {
        assert!(
            routing["rules"].as_array().unwrap().iter().any(|rule| {
                rule["outboundTag"] == "block"
                    && rule["user"]
                        .as_array()
                        .is_some_and(|users| users.contains(&json!(email)))
            }),
            "the running core lost an earlier revoked identity",
        );
    }
}

#[test]
fn readding_an_identity_removes_only_its_revocation_rule() {
    let mut host = Host::new(fetcher());
    let before = guarded_document(&[("u-a", UUID_A, ""), ("u-b", UUID_B, "")], &[]);
    host.apply(&before);
    host.apply(&guarded_document(&[("u-b", UUID_B, "")], &[]));
    let pass = host.apply(&before);
    assert!(pass.converged && pass.flow_error.is_none());
    let routing = host.exec.routing.lock().unwrap();
    assert!(routing["rules"].as_array().unwrap().iter().all(|rule| {
        !rule["ruleTag"]
            .as_str()
            .unwrap_or("")
            .starts_with("meridian-revoked-")
    }));
}

#[test]
fn a_core_restart_clears_revocations_and_needs_no_guard_install() {
    use meridian_agent::system::Exec;
    let mut host = Host::new(fetcher());
    host.apply(&guarded_document(
        &[("u-a", UUID_A, ""), ("u-b", UUID_B, "")],
        &[],
    ));
    let doc = guarded_document(&[("u-b", UUID_B, "")], &[]);
    host.apply(&doc);
    assert!(
        !String::from_utf8(host.read("/etc/netagent/core.json"))
            .unwrap()
            .contains("meridian-revoked-")
    );
    host.exec
        .run("systemctl", &["restart", "netcore.service"], &[])
        .unwrap();
    host.exec.clear();
    let pass = host.apply(&doc);
    assert!(pass.converged && pass.flow_error.is_none());
    assert!(
        !host
            .exec
            .calls()
            .iter()
            .any(|call| call.contains(" api adrules "))
    );
    let journal: Value = serde_json::from_slice(
        &std::fs::read(host.state.path().join("flow-revocations.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(journal["identities"], json!([]));
}

#[test]
fn a_partial_rule_failure_retries_the_same_graph_with_every_revocation() {
    let mut host = Host::new(fetcher());
    let third = "5f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00";
    host.apply(&guarded_document(
        &[("u-a", UUID_A, ""), ("u-b", UUID_B, ""), ("u-c", third, "")],
        &[],
    ));
    host.apply(&guarded_document(
        &[("u-b", UUID_B, ""), ("u-c", third, "")],
        &[],
    ));
    host.exec.routing_writes.lock().unwrap().clear();
    *host.exec.routing_failures.lock().unwrap() = 1;
    let pass = host.apply(&guarded_document(&[("u-c", third, "")], &[]));
    assert!(pass.converged && pass.flow_error.is_none(), "{pass:?}");
    let writes = host.exec.routing_writes.lock().unwrap();
    assert_eq!(writes.len(), 2);
    for write in writes.iter() {
        for email in [
            meridian_agent::xray_identity::email("u-a", UUID_A),
            meridian_agent::xray_identity::email("u-b", UUID_B),
        ] {
            assert!(
                write["routing"]["rules"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .any(|rule| {
                        rule["user"]
                            .as_array()
                            .is_some_and(|users| users.contains(&json!(email)))
                            && rule["outboundTag"] == "block"
                    })
            );
        }
    }
}

#[test]
fn an_unsaved_retry_intent_refuses_authentication_changes_without_a_restart() {
    let mut host = Host::new(fetcher());
    host.apply(&guarded_document(
        &[("u-a", UUID_A, ""), ("u-b", UUID_B, "")],
        &[],
    ));
    std::fs::write(
        host.state.path().join("flow-revocations.json"),
        b"secret-canary",
    )
    .unwrap();
    host.exec.clear();
    let pass = host.apply(&guarded_document(&[("u-b", UUID_B, "")], &[]));
    assert!(!pass.converged && pass.flow_error.is_some());
    assert!(
        host.exec
            .calls()
            .iter()
            .all(|call| !call.contains(" api rmu ") && !call.contains("systemctl restart"))
    );
    assert_eq!(clients(&host, 0).as_array().unwrap().len(), 2);
}

#[test]
fn reenable_uses_rule_removal_when_rule_installation_is_unavailable() {
    let mut host = Host::new(fetcher());
    let before = guarded_document(&[("u-a", UUID_A, ""), ("u-b", UUID_B, "")], &[]);
    host.apply(&before);
    host.apply(&guarded_document(&[("u-b", UUID_B, "")], &[]));
    *host.exec.routing_down.lock().unwrap() = true;
    host.exec.clear();
    let pass = host.apply(&before);
    assert!(pass.converged && pass.flow_error.is_none(), "{pass:?}");
    assert!(
        host.exec
            .calls()
            .iter()
            .any(|call| call.contains(" api rmrules "))
    );
    assert!(
        !host
            .exec
            .calls()
            .iter()
            .any(|call| call.contains("systemctl restart"))
    );
    let email = meridian_agent::xray_identity::email("u-a", UUID_A);
    assert!(
        host.exec.routing.lock().unwrap()["rules"]
            .as_array()
            .unwrap()
            .iter()
            .all(|rule| {
                !rule["user"]
                    .as_array()
                    .is_some_and(|users| users.contains(&json!(email)))
            })
    );
}

#[test]
fn a_failed_active_rule_removal_keeps_reenable_pending_without_adding_authentication() {
    let mut host = Host::new(fetcher());
    let before = guarded_document(&[("u-a", UUID_A, ""), ("u-b", UUID_B, "")], &[]);
    host.apply(&before);
    host.apply(&guarded_document(&[("u-b", UUID_B, "")], &[]));
    *host.exec.rule_removal_down.lock().unwrap() = true;
    host.exec.clear();
    let pass = host.apply(&before);
    assert!(!pass.converged && pass.flow_error.is_some());
    assert!(
        host.exec
            .calls()
            .iter()
            .all(|call| !call.contains(" api adu ") && !call.contains("systemctl restart"))
    );
    assert_eq!(clients(&host, 0).as_array().unwrap().len(), 1);
    *host.exec.rule_removal_down.lock().unwrap() = false;
    let repaired = host.apply(&before);
    assert!(repaired.converged && repaired.flow_error.is_none());
}
