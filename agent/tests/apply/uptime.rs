use super::*;
use meridian_agent::model::HealthCheck;
use meridian_agent::report;
use meridian_agent::system::Exec;
use meridian_agent::timeutil::now_unix;

fn write_proc(host: &Host, pid: u32, boot: u64, started_seconds: u64) {
    // SAFETY: sysconf has no pointer arguments; the host provides its clock tick rate.
    let ticks = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };
    assert!(ticks > 0);
    let proc = host.root.path().join("proc");
    std::fs::create_dir_all(proc.join(pid.to_string())).unwrap();
    std::fs::write(proc.join("stat"), format!("cpu 0 0 0 0\nbtime {boot}\n")).unwrap();
    std::fs::write(proc.join("uptime"), "20000.50 10000.20\n").unwrap();
    // The comm field can contain spaces and closing parentheses. starttime is field 22.
    let padding = vec!["0"; 18].join(" ");
    std::fs::write(
        proc.join(pid.to_string()).join("stat"),
        format!(
            "{pid} (netcore ) worker) S {padding} {} 0 0\n",
            started_seconds * ticks as u64
        ),
    )
    .unwrap();
}

fn check(host: &Host, doc: &[Value]) -> HealthCheck {
    report::health(
        host.root.path(),
        host.state.path(),
        &host.exec,
        &parse(doc),
        &host.observations,
    )
    .checks
    .into_iter()
    .find(|c| c.name == "xray.uptime")
    .expect("core uptime is distinct from host uptime")
}

fn up_seconds(check: &HealthCheck) -> u64 {
    assert_eq!(check.status, "pass");
    check
        .message
        .strip_prefix("up ")
        .unwrap()
        .strip_suffix('s')
        .unwrap()
        .parse()
        .unwrap()
}

#[test]
fn core_uptime_comes_from_the_main_process_start_time() {
    let mut host = Host::new(fetcher());
    let doc = full_document(&[]);
    assert!(host.apply(&doc).converged);
    let boot = now_unix() - 20_000;
    write_proc(&host, 42, boot, 19_995);
    let before = now_unix();
    let core = check(&host, &doc);
    let after = now_unix();
    let up = up_seconds(&core);
    assert!((before - boot - 19_995..=after - boot - 19_995).contains(&up));
    let health = report::health(
        host.root.path(),
        host.state.path(),
        &host.exec,
        &parse(&doc),
        &host.observations,
    );
    assert_eq!(health.uptime_seconds, 20_000);
    assert!(up < health.uptime_seconds);
}

#[test]
fn core_uptime_missing_process_evidence_warns_instead_of_inventing_zero() {
    let mut host = Host::new(fetcher());
    let doc = full_document(&[]);
    assert!(host.apply(&doc).converged);
    let core = check(&host, &doc);
    assert_eq!(core.status, "warn");
    assert_eq!(core.message, "core uptime unavailable");
}

#[test]
fn core_uptime_follows_a_new_main_pid_after_restart() {
    let mut host = Host::new(fetcher());
    let doc = full_document(&[]);
    assert!(host.apply(&doc).converged);
    let boot = now_unix() - 20_000;
    write_proc(&host, 42, boot, 19_950);
    let before = up_seconds(&check(&host, &doc));
    host.exec
        .run("systemctl", &["restart", "netcore.service"], &[])
        .unwrap();
    write_proc(&host, 43, boot, 19_998);
    assert!(up_seconds(&check(&host, &doc)) < before);
}
