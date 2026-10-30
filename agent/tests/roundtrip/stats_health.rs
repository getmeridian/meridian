use super::*;
use meridian_agent::model::HealthCheck;
use meridian_agent::timeutil::parse_rfc3339;

fn collection_check(agent: &Agent<'_>) -> HealthCheck {
    agent
        .build_report()
        .health
        .checks
        .into_iter()
        .find(|c| c.name == "stats")
        .expect("collection health is reported independently of stored traffic totals")
}

#[test]
fn stats_health_failures_keep_totals_but_become_warn_then_fail() {
    let pki = TestPki::new();
    let service = service(&pki);
    service.push(document(1, 1, &supported(), &pki));
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    *exec.stats_output.lock().unwrap() =
        r#"{"stat":[{"name":"user>>>u-a>>>traffic>>>uplink","value":"100"}]}"#.into();
    assert!(agent.send_stats().unwrap());
    let initial = collection_check(&agent);
    assert_eq!(initial.status, "pass");
    let last_collected = initial.message.strip_prefix("last collected ").unwrap();
    assert!(parse_rfc3339(last_collected).is_some());

    *exec.stats_down.lock().unwrap() = true;
    assert!(agent.send_stats().unwrap());
    let sent = service.requests_to("stats");
    assert_eq!(
        sent[1].json()["seq"],
        sent[0].json()["seq"].as_u64().unwrap() + 1
    );
    assert_eq!(sent[1].json()["users"], sent[0].json()["users"]);
    let failed = collection_check(&agent);
    assert_eq!(failed.status, "warn");
    assert!(
        failed
            .message
            .contains(&format!("last collected {last_collected}"))
    );
    assert!(!failed.message.contains("secret-canary"));
    assert!(failed.message.len() <= 300);
    *exec.elapsed.lock().unwrap() += Duration::from_secs(179);
    assert_eq!(collection_check(&agent).status, "warn");
    *exec.elapsed.lock().unwrap() += Duration::from_secs(1);
    assert_eq!(collection_check(&agent).status, "fail");

    // An empty successful query is collection success too.
    *exec.stats_down.lock().unwrap() = false;
    assert!(agent.send_stats().unwrap());
    assert_eq!(collection_check(&agent).status, "pass");
}

#[test]
fn stats_health_is_fresh_when_service_delivery_fails() {
    let pki = TestPki::new();
    let service = service(&pki);
    service.push(document(1, 1, &supported(), &pki));
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    drop(agent);
    let mut boot = node.read_bootstrap();
    boot["service_url"] = json!("http://127.0.0.1:9/s3cr3t");
    node.write_bootstrap(&boot);
    let mut offline = Agent::new(node.options(), &exec).unwrap();
    assert!(offline.send_stats().is_err());
    assert_eq!(collection_check(&offline).status, "pass");
}

#[test]
fn stats_health_no_success_is_warn_then_fail_without_an_invented_timestamp() {
    let pki = TestPki::new();
    let service = service(&pki);
    service.push(document(1, 1, &supported(), &pki));
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    assert_eq!(collection_check(&agent).status, "warn");
    assert!(
        collection_check(&agent)
            .message
            .contains("not collected yet")
    );
    *exec.elapsed.lock().unwrap() += Duration::from_secs(180);
    assert_eq!(collection_check(&agent).status, "fail");
}

#[test]
fn stats_health_counts_staleness_from_the_first_xray_not_the_agents_start() {
    let pki = TestPki::new();
    let service = service(&pki);
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    *exec.elapsed.lock().unwrap() += Duration::from_secs(600);
    assert!(!agent.send_stats().unwrap(), "no xray yet");
    service.push(document(1, 1, &supported(), &pki));
    agent.poll_once().unwrap();
    assert_eq!(collection_check(&agent).status, "warn");
}
