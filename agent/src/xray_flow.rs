//! Runtime routing for authenticated flows, including streams on an existing QUIC or mux session.
//!
//! AddRule replaces or appends; it cannot prepend. Send the reviewed routing graph
//! with revocations first. Eligible identities get copies of its rules, followed
//! by a managed-inbound denial and the original rules for API and bridge traffic.
//! The denial makes old-generation tombstones unnecessary after reconciliation.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fs;
use std::path::Path;

use serde_json::{Value, json};

use crate::apply::Engine;
use crate::compose::{self, UsersRef};
use crate::fsutil;
use crate::model::{Resource, ResourceError, Spec};
use crate::xray_identity;

fn clients(config: &Value, tag: &str) -> BTreeSet<String> {
    config
        .get("inbounds")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .find(|inbound| inbound.get("tag").and_then(Value::as_str) == Some(tag))
        .and_then(|inbound| inbound.pointer("/settings/clients"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|client| client.get("email").and_then(Value::as_str))
        .map(str::to_string)
        .collect()
}

fn tags(resources: &[Resource]) -> Vec<String> {
    resources
        .iter()
        .filter_map(|resource| match &resource.spec {
            Spec::XrayUsers(users) => Some(users.inbound_tag.clone()),
            Spec::Hysteria2Users(users) => Some(users.inbound_tag.clone()),
            _ => None,
        })
        .collect()
}

fn guarded(config: &Value, tags: &[String], previous: Option<&Value>) -> Result<Value, String> {
    let mut routing = config
        .get("routing")
        .cloned()
        .unwrap_or(json!({"rules": []}));
    let base = routing
        .get("rules")
        .and_then(Value::as_array)
        .ok_or("The routing rules are invalid.")?
        .clone();
    let default = config
        .get("outbounds")
        .and_then(Value::as_array)
        .and_then(|outbounds| outbounds.first())
        .and_then(|outbound| outbound.get("tag"))
        .and_then(Value::as_str)
        .ok_or("The default outbound needs a tag for the user flow guard.")?;
    let blocked = config
        .get("outbounds")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .find(|outbound| outbound.get("protocol").and_then(Value::as_str) == Some("blackhole"))
        .and_then(|outbound| outbound.get("tag"))
        .and_then(Value::as_str)
        .ok_or("The user flow guard needs a block outbound.")?;
    let mut revoked = Vec::new();
    let mut allowed = Vec::new();
    for tag in tags {
        let active = clients(config, tag);
        if let Some(previous) = previous {
            let mut people: BTreeMap<String, Vec<String>> = BTreeMap::new();
            for email in clients(previous, tag).difference(&active) {
                people
                    .entry(xray_identity::person_id(email).to_string())
                    .or_default()
                    .push(email.clone());
            }
            for (person, emails) in people {
                let key = crate::jcs::sha256_hex(format!("{person}/{tag}").as_bytes());
                revoked.push(json!({"type": "field", "user": emails, "inboundTag": [tag],
                    "outboundTag": blocked, "ruleTag": format!("meridian-block-{}", &key[..24])}));
            }
        }
        if active.is_empty() {
            continue;
        }
        for (index, original) in base.iter().enumerate() {
            if original
                .get("inboundTag")
                .and_then(Value::as_array)
                .is_some_and(|inbounds| !inbounds.iter().any(|inbound| inbound == tag))
            {
                continue;
            }
            let users: Vec<_> = active
                .iter()
                .filter(|email| {
                    original
                        .get("user")
                        .and_then(Value::as_array)
                        .is_none_or(|users| {
                            users.iter().any(|user| {
                                user.as_str().is_some_and(|user| {
                                    user.eq_ignore_ascii_case(email)
                                        || user
                                            .eq_ignore_ascii_case(xray_identity::person_id(email))
                                })
                            })
                        })
                })
                .cloned()
                .collect();
            if users.is_empty() {
                continue;
            }
            let mut rule = original.clone();
            rule["user"] = json!(users);
            rule["inboundTag"] = json!([tag]);
            let key = crate::jcs::sha256_hex(format!("{tag}/{index}").as_bytes());
            rule["ruleTag"] = json!(format!("meridian-allow-{}", &key[..24]));
            allowed.push(rule);
        }
        allowed.push(json!({"type": "field", "user": active, "inboundTag": [tag], "outboundTag": default,
            "ruleTag": format!("meridian-default-{}", &crate::jcs::sha256_hex(tag.as_bytes())[..24])}));
    }
    // Its content hash makes ListRule a check of this exact eligible-user graph.
    let digest =
        crate::jcs::sha256_hex(&serde_json::to_vec(&json!([&allowed, &base, tags])).unwrap());
    let mut rules = revoked;
    rules.extend(allowed);
    rules.push(
        json!({"type": "field", "inboundTag": tags, "outboundTag": blocked,
        "ruleTag": format!("meridian-guard-{}", &digest[..32])}),
    );
    rules.extend(base);
    routing["rules"] = json!(rules);
    let mut result = config.clone();
    result["routing"] = routing;
    Ok(result)
}

fn listed(routing: &Value) -> Vec<(String, String)> {
    routing
        .get("rules")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|rule| {
            (
                rule.get("ruleTag")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                rule.get("outboundTag")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
            )
        })
        .collect()
}

impl Engine<'_> {
    pub(crate) fn guard_live_users(
        &self,
        binary: &str,
        api: &str,
        composed: &Value,
        resources: &[Resource],
        previous: Option<&Value>,
    ) -> Result<(), String> {
        if !xray_identity::enabled(composed) {
            return Ok(());
        }
        let tags = tags(resources);
        let desired = guarded(composed, &tags, previous)?;
        let fallback = previous.and_then(|config| guarded(config, &tags, None).ok());
        self.install_guard(binary, api, &desired, fallback.as_ref().unwrap_or(composed))
    }

    fn install_guard(
        &self,
        binary: &str,
        api: &str,
        desired: &Value,
        fallback: &Value,
    ) -> Result<(), String> {
        let server = format!("--server={api}");
        let output = self
            .xray_api(binary, &["api", "lsrules", &server])
            .map_err(|_| "The Xray routing API is unavailable.")?;
        let response: Value = serde_json::from_str(&output)
            .map_err(|_| "The Xray routing API returned invalid rules.")?;
        let current: Vec<_> = response
            .get("rules")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .map(|rule| {
                (
                    rule.get("ruleTag")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string(),
                    rule.get("tag")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string(),
                )
            })
            .collect();
        if current == listed(&desired["routing"]) {
            return Ok(());
        }
        fs::create_dir_all(&self.cache_dir).map_err(|_| "Cannot prepare the user flow guard.")?;
        let path = self.cache_dir.join("flow-routing.json");
        let file = path.to_string_lossy().into_owned();
        let asset = Path::new(binary)
            .parent()
            .unwrap_or(Path::new("/"))
            .to_string_lossy();
        let env = [("XRAY_LOCATION_ASSET", asset.as_ref())];
        let result = (|| {
            fsutil::write_atomic(&path, &compose::render(desired), 0o600, None)
                .map_err(|_| "Cannot prepare the user flow guard.")?;
            let tested = self
                .exec
                .run(binary, &["run", "-test", "-c", &file], &env)
                .map_err(|_| "The user flow guard could not be checked.")?;
            if !tested.success {
                return Err("Xray rejected the user flow guard.");
            }
            let installed = self
                .exec
                .run(binary, &["api", "adrules", &server, &file], &env)
                .is_ok_and(|output| output.success);
            if !installed {
                // ReloadRules can clear the old rules before failing. Restore a complete,
                // validated graph, including API and balancers, without undoing user removal.
                if fsutil::write_atomic(&path, &compose::render(fallback), 0o600, None).is_ok() {
                    let _ = self
                        .exec
                        .run(binary, &["api", "adrules", &server, &file], &env);
                }
                return Err("The Xray routing API could not install the user flow guard.");
            }
            Ok(())
        })();
        let _ = fs::remove_file(&path);
        result.map_err(str::to_string)
    }

    pub(crate) fn flow_guard_result(
        &self,
        resources: &[Resource],
        statuses: &HashMap<String, &'static str>,
    ) -> Option<ResourceError> {
        let config = resources.iter().find_map(|resource| match &resource.spec {
            Spec::XrayConfig(config) => Some(config),
            _ => None,
        })?;
        if tags(resources).is_empty() {
            return None;
        }
        if !xray_identity::enabled(&config.config) {
            return Some(ResourceError {
                code: "unsupported".into(),
                message: "Apply the fleet to enable Xray's RoutingService.".into(),
                retryable: false,
            });
        }
        let result = (|| {
            if statuses.get(&config.id) != Some(&"applied") {
                return Err("The node has not applied its Xray configuration.".to_string());
            }
            let (binary, _) = crate::xray_health::api(&self.root, resources)
                .ok_or("The node's Xray API is not installed.")?;
            let users: Vec<_> = resources
                .iter()
                .filter_map(|resource| match &resource.spec {
                    Spec::XrayUsers(users) => Some(UsersRef::Vless(users)),
                    Spec::Hysteria2Users(users) => Some(UsersRef::Hysteria2(users)),
                    _ => None,
                })
                .collect();
            let composed = compose::compose(&config.config, &users)?;
            self.guard_live_users(&binary, &config.api_address, &composed, resources, None)
        })();
        result.err().map(|message| ResourceError {
            code: "xray_api_failed".into(),
            message,
            retryable: true,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_revocation_precedes_every_rule_and_keeps_balancers_and_api_routes() {
        let before = json!({"api": {"services": ["RoutingService"]},
            "inbounds": [{"tag": "hy2", "settings": {"clients": [{"email": "u-a.c123"}, {"email": "u-b.c456"}]}}],
            "outbounds": [{"tag": "direct"}, {"tag": "block", "protocol": "blackhole"}],
            "routing": {"balancers": [{"tag": "pool", "selector": ["exit-"]}], "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                {"type": "field", "network": "tcp", "balancerTag": "pool"}]}});
        let mut after = before.clone();
        after["inbounds"][0]["settings"]["clients"] = json!([{"email": "u-b.c456"}]);
        let config = guarded(&after, &["hy2".into()], Some(&before)).unwrap();
        assert_eq!(config["routing"]["rules"][0]["user"], json!(["u-a.c123"]));
        assert_eq!(config["routing"]["rules"][0]["outboundTag"], "block");
        assert_eq!(
            config["routing"]["balancers"],
            before["routing"]["balancers"]
        );
        let rules = config["routing"]["rules"].as_array().unwrap();
        assert_eq!(
            &rules[rules.len() - 2..],
            before["routing"]["rules"].as_array().unwrap()
        );
        let rebuilt = guarded(&after, &["hy2".into()], None).unwrap();
        assert!(
            !rebuilt.to_string().contains("u-a.c123"),
            "revoked generations must not accumulate"
        );
    }
}
