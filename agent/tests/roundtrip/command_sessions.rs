//! A session may disappear during reports or host actions in a command batch.

use super::*;
use meridian_agent::timeutil::parse_rfc3339;
use std::panic::{AssertUnwindSafe, catch_unwind};

#[test]
fn review_1_1_report_session_loss_never_panics_and_reopens_before_commands() {
    for reopen_fails in [false, true] {
        let pki = TestPki::new();
        let service = ready_service(&pki);
        let node = Node::new(&service.url(), &pki);
        let exec = FakeExec::default();
        let mut agent = Agent::new(node.options(), &exec).unwrap();
        agent.poll_once().unwrap();
        exec.clear();
        service.push(document(1, 2, &supported(), &pki));
        push(
            &service,
            &command(
                &pki,
                json!({"kind": "restart_unit", "resource_id": "unit:netcore"}),
                1,
                now_unix() + 300,
            ),
        );
        {
            let mut state = service.state.lock().unwrap();
            state
                .failures
                .insert("/report", [(401, "unauthenticated")].into());
            let attempts = if reopen_fails { 2 } else { 1 };
            state.failures.insert(
                "/hello",
                vec![(503, "temporarily_unavailable"); attempts].into(),
            );
        }
        let poll = catch_unwind(AssertUnwindSafe(|| agent.poll_once()));
        assert!(
            poll.is_ok(),
            "a lost report session must never panic the daemon"
        );
        if reopen_fails {
            assert!(poll.unwrap().is_err());
            assert_eq!(exec.count("systemctl restart netcore.service"), 0);
            assert!(!node.state.path().join("commands.json").exists());
            agent.poll_once().unwrap();
        } else {
            poll.unwrap().unwrap();
        }
        assert_eq!(exec.count("systemctl restart netcore.service"), 1);
        assert_eq!(result(&service)["status"], "succeeded");
    }
}

#[test]
fn review_1_2_failed_stats_keep_the_command_completion_on_the_service_clock() {
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
    {
        let mut state = service.state.lock().unwrap();
        state
            .failures
            .insert("/stats", [(401, "unauthenticated")].into());
        state
            .failures
            .insert("/hello", [(503, "temporarily_unavailable")].into());
    }
    agent.poll_once().unwrap();
    assert_eq!(exec.count("systemctl restart netcore.service"), 1);
    let observed = result(&service);
    let finished = parse_rfc3339(observed["finished_at"].as_str().unwrap()).unwrap();
    assert!(
        finished >= server_now && finished < server_now + 300,
        "completion lost the service offset: {observed}"
    );
}

#[test]
fn review_1_3_renew_reopens_a_session_expired_during_the_poll_once() {
    let pki = TestPki::new();
    let service = ready_service(&pki);
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    let before = std::fs::read(node.state.path().join("identity.json")).unwrap();
    service.state.lock().unwrap().expire_after_poll = true;
    push(
        &service,
        &command(
            &pki,
            json!({"kind": "renew_credential"}),
            1,
            now_unix() + 300,
        ),
    );
    agent.poll_once().unwrap();
    assert_eq!(result(&service)["status"], "succeeded");
    assert_eq!(service.requests_to("renew").len(), 2);
    assert_eq!(service.requests_to("hello").len(), 2);
    assert_ne!(
        std::fs::read(node.state.path().join("identity.json")).unwrap(),
        before
    );
}
