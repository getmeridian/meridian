//! Every core restart/reset refuses an unapplied document or stale credentials on disk.

use super::*;

fn refuse_core_command(defect: &str) {
    for kind in ["restart_unit", "reset_inbound"] {
        let pki = TestPki::new();
        let service = ready_service(&pki);
        let node = Node::new(&service.url(), &pki);
        let exec = FakeExec::default();
        let mut agent = Agent::new(node.options(), &exec).unwrap();
        agent.poll_once().unwrap();
        let path = node.root.path().join("etc/netagent/core.json");
        match defect {
            "unapplied" => agent.state.applied = None,
            "stale-config" => {
                let mut config: Value =
                    serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
                config["inbounds"][0]["settings"]["clients"][0]["id"] =
                    json!("4f0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00");
                std::fs::write(&path, serde_json::to_vec(&config).unwrap()).unwrap();
            }
            "missing-config" => std::fs::remove_file(&path).unwrap(),
            _ => unreachable!(),
        }
        exec.clear();
        let fields = if kind == "restart_unit" {
            json!({"kind": kind, "resource_id": "unit:netcore"})
        } else {
            json!({"kind": kind, "inbound_tag": "vless-reality"})
        };
        push(&service, &command(&pki, fields, 1, now_unix() + 300));
        agent.poll_once().unwrap();
        let observed = result(&service);
        assert_eq!(observed["status"], "failed", "{kind}/{defect}: {observed}");
        assert_eq!(
            observed["error"]["retryable"], true,
            "{kind}/{defect}: {observed}"
        );
        assert!(
            exec.calls()
                .iter()
                .all(|call| !call.contains("systemctl restart")
                    && !call.contains(" api rmi ")
                    && !call.contains(" api adi ")),
            "{kind}/{defect}: {:?}",
            exec.calls()
        );
    }
}

#[test]
fn core_commands_refuse_an_unapplied_document() {
    refuse_core_command("unapplied");
}

#[test]
fn core_commands_refuse_stale_credentials_on_disk() {
    refuse_core_command("stale-config");
}

#[test]
fn core_commands_refuse_a_missing_composed_config() {
    refuse_core_command("missing-config");
}
