//! Execution IDs and unsent outcomes survive the command's admission deadline.

use super::*;
use meridian_agent::commands::CommandState;
use std::time::Instant;

#[test]
fn review_2_2_unacknowledged_results_retry_after_expiry_without_reexecution() {
    let pki = TestPki::new();
    let service = ready_service(&pki);
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    exec.clear();
    service.state.lock().unwrap().result_refusals = 1;
    let expiry = now_unix() + 60;
    push(
        &service,
        &command(
            &pki,
            json!({"kind": "restart_unit", "resource_id": "unit:netcore"}),
            1,
            expiry,
        ),
    );
    assert!(agent.poll_once().is_err());
    let first = result(&service);
    {
        let mut state = service.state.lock().unwrap();
        state.clock_offset = 90;
        state.sessions.clear();
    }
    drop(agent);
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    assert_eq!(
        service.requests_to("command-results").len(),
        2,
        "the expired result must still be retried"
    );
    assert_eq!(result(&service), first);
    let saved = CommandState::load(node.state.path()).unwrap();
    assert!(saved.find(ID).unwrap().acknowledged);
    assert_eq!(exec.count("systemctl restart netcore.service"), 1);
}

#[test]
fn result_retention_is_bounded_and_counts_from_a_late_completion() {
    use meridian_agent::commands::{CommandResult, CommandStatus, RememberedCommand};
    for acknowledged in [false, true] {
        let expiry = 1_700_000_060;
        let finished = expiry + 30;
        let mut state = CommandState {
            results: vec![RememberedCommand {
                digest: "0".repeat(64),
                expires_at: expiry,
                result: CommandResult::new(ID, CommandStatus::Succeeded, finished),
                acknowledged,
            }],
        };
        state.prune(expiry + 7 * 86400);
        assert!(
            state.find(ID).is_some(),
            "a late completion retains its delivery window"
        );
        state.prune(finished + 7 * 86400);
        assert!(
            state.results.is_empty(),
            "retention must have a bounded age"
        );
    }
}

#[test]
fn review_2_3_one_second_clock_rounding_and_rehello_never_repeat_execution() {
    let pki = TestPki::new();
    let service = ready_service(&pki);
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    exec.clear();
    let expiry = now_unix() + 4;
    let restart = command(
        &pki,
        json!({"kind": "restart_unit", "resource_id": "unit:netcore"}),
        1,
        expiry,
    );
    push(&service, &restart);
    service.state.lock().unwrap().failures.insert(
        "/command-results",
        [(503, "temporarily_unavailable")].into(),
    );
    assert!(agent.poll_once().is_err());
    let first = result(&service);
    assert_eq!(first["status"], "succeeded");
    assert_eq!(exec.count("systemctl restart netcore.service"), 1);
    // Enter the one-second window without relying on the subsecond phase at test startup.
    let wait_until = Instant::now() + Duration::from_secs(5);
    while now_unix() < expiry - 1 && Instant::now() < wait_until {
        std::thread::sleep(Duration::from_millis(5));
    }
    assert_eq!(now_unix(), expiry - 1);
    {
        let mut state = service.state.lock().unwrap();
        state.sessions.clear();
        state.clock_offset = 1;
        state.clock_on_poll = Some(0);
        state
            .failures
            .insert("/poll", [(401, "unauthenticated")].into());
    }
    drop(agent);
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    assert_eq!(
        exec.count("systemctl restart netcore.service"),
        1,
        "a rounded expiry must not erase an executed ID before a server redelivery"
    );
    assert_eq!(result(&service), first);
    let mut saved = CommandState::load(node.state.path()).unwrap();
    saved.prune(expiry + 1);
    assert!(
        saved.find(ID).is_some(),
        "acknowledged IDs must also survive the redelivery window"
    );
}
