use super::*;
use meridian_agent::report;

#[test]
fn core_api_an_active_unit_with_an_unavailable_api_is_not_applied() {
    let mut host = Host::new(fetcher());
    *host.exec.stats_down.lock().unwrap() = true;
    let pass = host.apply(&full_document(&[]));
    assert_eq!(
        status(&pass, "unit:netcore"),
        ("failed".into(), Some("service_failed".into()))
    );
    let error = pass.observations.last().unwrap().error.as_ref().unwrap();
    assert!(error.message.contains("API"), "{}", error.message);
    assert!(!error.message.contains("secret-canary"));
    assert_eq!(*host.exec.elapsed.lock().unwrap(), Duration::from_secs(10));
    assert!(
        host.exec
            .timeouts
            .lock()
            .unwrap()
            .iter()
            .all(|timeout| *timeout <= Duration::from_secs(1))
    );
}

#[test]
fn core_api_a_failed_restart_restores_the_previous_config() {
    let mut host = Host::new(fetcher());
    let doc = full_document(&[]);
    assert!(host.apply(&doc).converged);
    let before = host.read("/etc/netagent/core.json");
    *host.exec.stats_down_when.lock().unwrap() = Some((
        host.root.path().join("etc/netagent/core.json"),
        "\"debug\"".into(),
    ));
    let mut next = doc;
    next[5]["config"]["log"]["loglevel"] = json!("debug");
    let pass = host.apply(&next);
    assert!(!pass.converged);
    let error = pass.observations.last().unwrap().error.as_ref().unwrap();
    assert!(
        error
            .message
            .contains("restored the previous xray:config and it runs again"),
        "{}",
        error.message
    );
    assert_eq!(host.read("/etc/netagent/core.json"), before);
    assert!(host.exec.unit("netcore.service").active);
}

#[test]
fn core_api_retries_without_resetting_traffic() {
    let mut host = Host::new(fetcher());
    *host.exec.stats_failures.lock().unwrap() = 2;
    *host.exec.stats_output.lock().unwrap() =
        r#"{"stat":[{"name":"user>>>u-a>>>traffic>>>uplink","value":"9"}]}"#.into();
    assert!(host.apply(&full_document(&[])).converged);
    let calls = host.exec.calls();
    let probes: Vec<_> = calls
        .iter()
        .filter(|c| c.contains(" api statsquery "))
        .collect();
    assert_eq!(probes.len(), 3);
    assert!(probes.iter().all(|c| !c.contains("-reset")));
    assert!(host.exec.stats_output.lock().unwrap().contains("9"));
    let sleeps = host.exec.sleeps.lock().unwrap();
    assert!(sleeps.windows(2).all(|p| p[0] < p[1]));
}

#[test]
fn core_api_a_pid_change_aborts_before_retrying() {
    let mut host = Host::new(fetcher());
    *host.exec.stats_restart.lock().unwrap() = true;
    *host.exec.stats_failures.lock().unwrap() = 1;
    let pass = host.apply(&full_document(&[]));
    assert!(!pass.converged);
    assert!(
        pass.observations
            .last()
            .unwrap()
            .error
            .as_ref()
            .unwrap()
            .message
            .contains("process changed")
    );
    assert_eq!(
        host.exec
            .calls()
            .iter()
            .filter(|c| c.contains(" api statsquery "))
            .count(),
        1
    );
}

#[test]
fn core_api_a_process_exit_aborts_before_retrying() {
    let mut host = Host::new(fetcher());
    *host.exec.stats_exit.lock().unwrap() = true;
    *host.exec.stats_failures.lock().unwrap() = 1;
    let pass = host.apply(&full_document(&[]));
    assert!(!pass.converged);
    assert_eq!(
        host.exec
            .calls()
            .iter()
            .filter(|c| c.contains(" api statsquery "))
            .count(),
        1
    );
}

#[test]
fn core_api_a_pid_change_during_a_successful_query_is_not_ready() {
    let mut host = Host::new(fetcher());
    *host.exec.stats_restart.lock().unwrap() = true;
    assert!(!host.apply(&full_document(&[])).converged);
}

#[test]
fn core_api_invalid_json_cannot_prove_readiness() {
    let mut host = Host::new(fetcher());
    *host.exec.stats_output.lock().unwrap() = "null".into();
    assert!(!host.apply(&full_document(&[])).converged);
}

#[test]
fn core_api_health_reports_a_safe_failure_and_recovers() {
    let mut host = Host::new(fetcher());
    let doc = full_document(&[]);
    assert!(host.apply(&doc).converged);
    *host.exec.stats_down.lock().unwrap() = true;
    let health = report::health(
        host.root.path(),
        host.state.path(),
        &host.exec,
        &parse(&doc),
        &host.observations,
    );
    let check = health
        .checks
        .iter()
        .find(|c| c.name == "xray.api")
        .expect("core API readiness is reported");
    assert_eq!(check.status, "fail");
    assert!(!check.message.contains("secret-canary"));
    assert!(check.message.len() <= 300);
    assert_eq!(health.status, "unhealthy");
    *host.exec.stats_down.lock().unwrap() = false;
    let health = report::health(
        host.root.path(),
        host.state.path(),
        &host.exec,
        &parse(&doc),
        &host.observations,
    );
    assert_eq!(
        health
            .checks
            .iter()
            .find(|c| c.name == "xray.api")
            .unwrap()
            .status,
        "pass"
    );
}

#[test]
fn core_api_health_stays_within_the_check_budget() {
    let mut host = Host::new(fetcher());
    let mut doc = full_document(&[]);
    assert!(host.apply(&doc).converged);
    for i in 0..40 {
        doc.push(unit(
            &format!("unit:net{i}"),
            &format!("net{i}.service"),
            &[],
            &[],
        ));
    }
    let health = report::health(
        host.root.path(),
        host.state.path(),
        &host.exec,
        &parse(&doc),
        &host.observations,
    );
    assert_eq!(health.checks.len(), 32);
    assert!(health.checks.iter().any(|c| c.name == "xray.api"));
}
