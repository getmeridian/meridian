//! A held, signed deadline protects this host without another response from the service.
use super::*;
use meridian_agent::timeutil::now_unix;

fn guarded_users(deadline: u64) -> Vec<Value> {
    let mut res = supported();
    res[1] = xray_config(&["pin:xray"], None);
    res[1]["config"]["api"] =
        json!({"services":["HandlerService","StatsService","RoutingService"]});
    res[1]["config"]["outbounds"] =
        json!([{"tag":"direct","protocol":"freedom"},{"tag":"deny","protocol":"blackhole"}]);
    res[1]["config"]["routing"] = json!({"rules":[]});
    let other = "4f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00";
    res[2] = vless_users(&[("u-a", UUID_A, ""), ("u-b", other, "")]);
    let mut hy = hy2_users(&[("u-a", UUID_A), ("u-b", other)]);
    res[2]["users"][0]["expires_unix"] = json!(deadline);
    hy["users"][0]["expires_unix"] = json!(deadline);
    res.insert(3, hy);
    res
}

fn accounts(node: &Node) -> Vec<usize> {
    let file: Value = serde_json::from_slice(
        &std::fs::read(node.root.path().join("etc/netagent/core.json")).unwrap(),
    )
    .unwrap();
    file["inbounds"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|i| i.pointer("/settings/clients")?.as_array().map(Vec::len))
        .collect()
}

#[test]
fn offline_restart_expiry_and_renewal_change_only_new_connections() {
    let pki = TestPki::new();
    let service = service(&pki);
    // Retain a correct service clock even when the restarted node cannot say hello.
    service.state.lock().unwrap().clock_offset = 90;
    let due = now_unix() + 93;
    let before = guarded_users(due);
    service.push(document(1, 1, &before, &pki));
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    assert_eq!(accounts(&node), [2, 2]);
    drop(agent);
    let requests = service.endpoints();
    std::thread::sleep(Duration::from_secs(3));
    exec.clear();
    let mut restarted = Agent::new(node.options(), &exec).unwrap();
    // Even if the live API fails during expiry, no other stream may be cut off.
    *exec.api_down.lock().unwrap() = true;
    restarted.apply_held().unwrap();
    assert!(
        restarted
            .observations()
            .iter()
            .any(|o| o.status == "failed")
    );
    assert!(!exec.calls().iter().any(|s| s.contains("systemctl restart")));
    exec.clear();
    *exec.api_down.lock().unwrap() = false;
    restarted.apply_held().unwrap(); // no session and not even a hello
    assert_eq!(requests, service.endpoints());
    assert_eq!(accounts(&node), [1, 1]);
    let calls = exec.calls();
    assert!(
        calls.iter().any(|s| s.contains(" api adrules ")),
        "{calls:?}"
    );
    assert_eq!(calls.iter().filter(|s| s.contains(" api rmu ")).count(), 2);
    assert!(
        !calls
            .iter()
            .any(|s| s.contains("systemctl restart") || s.contains(" api rmi "))
    );
    assert_eq!(restarted.state.held_ref(), restarted.state.applied);
    restarted.apply_held().unwrap();
    assert_eq!(accounts(&node), [1, 1]);
    let mut renewal = guarded_users(due + 3600);
    // This document adds the renewed person AND carries another expired identity.
    // Absent removals cannot stand in for a successful addition in a mixed apply.
    let other_key = "5f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00";
    renewal[2]["users"]
        .as_array_mut()
        .unwrap()
        .push(json!({"user_id":"u-c","uuid":other_key,"flow":"","expires_unix":due}));
    renewal[3]["users"]
        .as_array_mut()
        .unwrap()
        .push(json!({"user_id":"u-c","auth":other_key,"expires_unix":due}));
    *exec.api_down.lock().unwrap() = true;
    service.push(document(1, 2, &renewal, &pki));
    restarted.poll_once().unwrap();
    assert_ne!(restarted.state.held_ref(), restarted.state.applied);
    assert_eq!(accounts(&node), [1, 1]);
    *exec.api_down.lock().unwrap() = false;
    restarted.apply_held().unwrap();
    assert_eq!(restarted.state.held_ref(), restarted.state.applied);
    assert_eq!(accounts(&node), [2, 2]);
}
