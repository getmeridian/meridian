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
