use super::*;

fn write_host(node: &Node, name: &str, text: &str) {
    let path = node.root.path().join(name.trim_start_matches('/'));
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, text).unwrap();
}

#[test]
fn capacity_reports_wait_for_negotiation_and_preserve_unavailable_fields() {
    for accepted in [false, true] {
        let pki = TestPki::new();
        let service = service(&pki);
        if accepted {
            service
                .state
                .lock()
                .unwrap()
                .capabilities
                .push("capacity_telemetry");
        }
        let node = Node::new(&service.url(), &pki);
        write_host(&node, "/proc/stat", "cpu 0 0 0\ncpu0 0 0 0\ncpu1 0 0 0\n");
        write_host(&node, "/proc/loadavg", "0.00 0.50 1.250 1/10 42\n");
        write_host(
            &node,
            "/proc/meminfo",
            "MemTotal: 4096 kB\nMemAvailable: 0 kB\n",
        );
        let exec = FakeExec::default();
        let mut agent = Agent::new(node.options(), &exec).unwrap();
        agent.send_report().unwrap();
        let report = service.requests_to("report")[0].json();
        if !accepted {
            assert!(report.get("capacity").is_none());
            continue;
        }
        let capacity = &report["capacity"];
        assert_eq!(capacity["cpu_count"], 2);
        assert_eq!(capacity["load1_milli"], 0);
        assert_eq!(capacity["memory_available_bytes"], 0);
        assert_eq!(capacity["memory_total_bytes"], 4_194_304);
        assert_eq!(capacity["network"], Value::Null);
        assert_eq!(capacity["xray"], Value::Null);
        assert_eq!(capacity["inbound_connections"], Value::Null);
        assert_eq!(capacity["sequence"], 1);
    }
}

fn network(node: &Node, interface: &str, rx: u64, tx: u64) {
    write_host(
        node,
        "/proc/net/route",
        &format!(
            "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n{interface} 00000000 00000000 0001 0 0 10 00000000 0 0 0\n"
        ),
    );
    write_host(
        node,
        &format!("/sys/class/net/{interface}/ifindex"),
        if interface == "eth0" { "2\n" } else { "3\n" },
    );
    write_host(
        node,
        &format!("/sys/class/net/{interface}/statistics/rx_bytes"),
        &rx.to_string(),
    );
    write_host(
        node,
        &format!("/sys/class/net/{interface}/statistics/tx_bytes"),
        &tx.to_string(),
    );
}

#[test]
fn host_counters_reset_epochs_on_wraparound_interface_changes_and_safe_integer_overflow() {
    let pki = TestPki::new();
    let service = service(&pki);
    service
        .state
        .lock()
        .unwrap()
        .capabilities
        .push("capacity_telemetry");
    let node = Node::new(&service.url(), &pki);
    let exec = FakeExec::default();
    network(&node, "eth0", u64::MAX - 10, u64::MAX - 20);
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.send_report().unwrap();
    let first = service.requests_to("report")[0].json()["capacity"]["network"].clone();
    assert_eq!(first["rx_bytes"], 0);
    *exec.elapsed.lock().unwrap() += Duration::from_secs(2);
    network(&node, "eth0", u64::MAX - 4, u64::MAX - 10);
    agent.send_report().unwrap();
    let second = service.requests_to("report")[1].json()["capacity"]["network"].clone();
    assert_eq!(second["rx_bytes"], 6);
    assert_eq!(second["rx_bytes_per_second"], 3);
    assert_eq!(second["tx_bytes_per_second"], 5);
    assert_eq!(first["counter_epoch"], second["counter_epoch"]);
    *exec.elapsed.lock().unwrap() += Duration::from_secs(1);
    network(&node, "eth0", 4, 10);
    agent.send_report().unwrap();
    let wrapped = service.requests_to("report")[2].json()["capacity"]["network"].clone();
    assert_ne!(wrapped["counter_epoch"], second["counter_epoch"]);
    assert_eq!(wrapped["rx_bytes_per_second"], Value::Null);
    *exec.elapsed.lock().unwrap() += Duration::from_secs(1);
    network(&node, "eth1", 100, 200);
    agent.send_report().unwrap();
    let changed = service.requests_to("report")[3].json()["capacity"]["network"].clone();
    assert_ne!(changed["counter_epoch"], wrapped["counter_epoch"]);
    assert_eq!(changed["rx_bytes"], 0);
    *exec.elapsed.lock().unwrap() += Duration::from_secs(1);
    network(&node, "eth1", 100 + (1 << 53), 200);
    agent.send_report().unwrap();
    let overflow = service.requests_to("report")[4].json()["capacity"]["network"].clone();
    assert_ne!(overflow["counter_epoch"], changed["counter_epoch"]);
    assert_eq!(overflow["rx_bytes"], 0);
    assert_eq!(overflow["rx_bytes_per_second"], Value::Null);
    let text = String::from_utf8(service.requests_to("report")[4].body.clone()).unwrap();
    assert!(!text.contains("eth1") && !text.contains("ifindex"));
}

#[test]
fn process_capacity_uses_the_managed_pid_without_querying_online_users() {
    let pki = TestPki::new();
    let service = service(&pki);
    service
        .state
        .lock()
        .unwrap()
        .capabilities
        .push("capacity_telemetry");
    service.push(document(1, 1, &supported(), &pki));
    let node = Node::new(&service.url(), &pki);
    // comm may itself contain spaces and a closing parenthesis.
    write_host(
        &node,
        "/proc/42/stat",
        &format!(
            "42 (netcore ) worker) S {} 100 0 0",
            vec!["0"; 18].join(" ")
        ),
    );
    write_host(
        &node,
        "/proc/42/status",
        "Name:\tnetcore\nVmRSS:\t1024 kB\n",
    );
    write_host(&node, "/proc/uptime", "100.00 50.0\n");
    let exec = FakeExec::default();
    let mut agent = Agent::new(node.options(), &exec).unwrap();
    agent.poll_once().unwrap();
    let report = service.requests_to("report")[0].json();
    assert_eq!(
        report["capacity"]["xray"]["memory_resident_bytes"],
        1_048_576
    );
    assert!(
        report["capacity"]["xray"]["uptime_seconds"]
            .as_u64()
            .is_some()
    );
    assert!(exec.calls().iter().all(|call| !call.contains("online")));
}
