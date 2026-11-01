//! Block only revoked identities, ahead of the reviewed routing rules.
//! An active-user catch-all would suppress Xray's IPIfNonMatch DNS pass.

use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::path::Path;

use serde_json::{Value, json};

use crate::apply::Engine;
use crate::compose::{self, UsersRef};
use crate::fsutil;
use crate::model::{Resource, ResourceError, Spec, XrayConfigSpec};
use crate::xray_identity;
use crate::xray_revocations::{LIMIT_MESSAGE, Revocation, Revocations, core_invocation, lost};
use crate::xray_revocations::{active_identities, clients};

pub(crate) struct GuardFailure {
    pub message: String,
    /// Authentication waits for durable retry intent and removal of active identity blocks.
    pub can_apply_authentication: bool,
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

fn guarded(config: &Value, revoked: &BTreeSet<Revocation>) -> Result<Value, String> {
    if revoked.is_empty() {
        return Ok(config.clone());
    }
    let mut routing = config
        .get("routing")
        .cloned()
        .unwrap_or(json!({"rules": []}));
    let base = routing
        .get("rules")
        .and_then(Value::as_array)
        .ok_or("The routing rules are invalid.")?
        .clone();
    let blocked = config
        .get("outbounds")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .find(|outbound| outbound.get("protocol").and_then(Value::as_str) == Some("blackhole"))
        .and_then(|outbound| outbound.get("tag"))
        .and_then(Value::as_str)
        .ok_or("Blocking revoked identities needs a blackhole outbound.")?;
    let mut rules: Vec<Value> = revoked
        .iter()
        .map(|identity| {
            json!({
                "type": "field", "user": [&identity.email], "inboundTag": [&identity.tag],
                "outboundTag": blocked, "ruleTag": identity.rule_tag(),
            })
        })
        .collect();
    rules.extend(base);
    routing["rules"] = json!(rules);
    let mut result = config.clone();
    result["routing"] = routing;
    Ok(result)
}

type Listed = Vec<(String, String)>;

fn listed(routing: &Value) -> Listed {
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

struct FlowContext<'a> {
    config: &'a XrayConfigSpec,
    binary: String,
    composed: Value,
}

fn flow_error(message: String) -> ResourceError {
    ResourceError {
        code: "xray_api_failed".into(),
        message,
        retryable: true,
    }
}

impl Engine<'_> {
    fn live_rules(&self, binary: &str, api: &str) -> Result<Listed, String> {
        let server = format!("--server={api}");
        let output = self
            .xray_api(binary, &["api", "lsrules", &server])
            .map_err(|_| "The Xray routing API is unavailable.")?;
        let response: Value = serde_json::from_str(&output)
            .map_err(|_| "The Xray routing API returned invalid rules.")?;
        if response.get("rules").is_some_and(|rules| !rules.is_array()) {
            return Err("The Xray routing API returned invalid rules.".into());
        }
        Ok(response
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
            .collect())
    }

    pub(crate) fn guard_live_users(
        &self,
        binary: &str,
        api: &str,
        composed: &Value,
        resources: &[Resource],
        previous: Option<&Value>,
    ) -> Result<(), GuardFailure> {
        if !xray_identity::enabled(composed) {
            return Ok(());
        }

        let mut can_apply_authentication = false;
        let result = (|| {
            let invocation = core_invocation(self.exec)?;
            let path = self
                .cache_dir
                .parent()
                .ok_or("The core revocation retry directory is unavailable.")?
                .join("flow-revocations.json");
            let before = Revocations::load(&path)?;
            let mut revoked = before.clone();
            revoked.for_invocation(&invocation);
            let tags = tags(resources);
            let additions = previous.is_some_and(|before| {
                tags.iter()
                    .any(|tag| !clients(composed, tag).is_subset(&clients(before, tag)))
            });
            let removed = previous
                .map(|previous| lost(composed, &tags, previous))
                .unwrap_or_default();
            // Removals may proceed when listing is down, after saving their retry
            // intent. Additions wait until no live block can match their identity.
            if !additions {
                revoked.add(removed.clone(), &json!({}))?;
                revoked.save_if_changed(&path, &before)?;
                can_apply_authentication = true;
            }
            let mut current = self.live_rules(binary, api)?;
            let existing: Vec<_> = current
                .iter()
                .map(|(tag, _)| Revocation::from_rule_tag(tag))
                .collect::<Result<Vec<_>, _>>()?
                .into_iter()
                .flatten()
                .collect();
            let saved = revoked.clone();
            revoked.add(existing.clone(), &json!({}))?;
            revoked.save_if_changed(&path, &saved)?;
            let active = active_identities(composed, existing.iter());
            if !active.is_empty() {
                can_apply_authentication = false;
                let names: Vec<_> = active.iter().map(Revocation::rule_tag).collect();
                let server = format!("--server={api}");
                for chunk in names.chunks(32) {
                    let mut args = vec!["api", "rmrules", server.as_str()];
                    args.extend(chunk.iter().map(String::as_str));
                    self.xray_api(binary, &args).map_err(
                        |_| "The routing API could not remove a block for a re-enabled identity.",
                    )?;
                }
                current = self.live_rules(binary, api)?;
                let remaining: Vec<_> = current
                    .iter()
                    .map(|(tag, _)| Revocation::from_rule_tag(tag))
                    .collect::<Result<Vec<_>, _>>()?
                    .into_iter()
                    .flatten()
                    .collect();
                if !active_identities(composed, remaining.iter()).is_empty() {
                    return Err("The core still holds a block for a re-enabled identity.".into());
                }
            }
            let saved = revoked.clone();
            revoked.add(removed, composed)?;
            revoked.save_if_changed(&path, &saved)?;
            can_apply_authentication = true;
            let desired = guarded(composed, &revoked.identities)?;
            if current != listed(&desired["routing"]) {
                self.install_guard(binary, api, &desired)?;
            }
            if core_invocation(self.exec)? != invocation {
                return Err("The core restarted while revocation rules were being checked.".into());
            }
            if revoked.overflow {
                return Err(LIMIT_MESSAGE.into());
            }
            Ok(())
        })();
        result.map_err(|message| GuardFailure {
            message,
            can_apply_authentication,
        })
    }

    fn install_guard(&self, binary: &str, api: &str, desired: &Value) -> Result<(), String> {
        fs::create_dir_all(&self.cache_dir).map_err(|_| "Cannot prepare the revocation rules.")?;
        let path = self.cache_dir.join("flow-routing.json");
        let file = path.to_string_lossy().into_owned();
        let asset = Path::new(binary)
            .parent()
            .unwrap_or(Path::new("/"))
            .to_string_lossy();
        let env = [("XRAY_LOCATION_ASSET", asset.as_ref())];
        let server = format!("--server={api}");
        let result = (|| {
            fsutil::write_atomic(&path, &compose::render(desired), 0o600, None)
                .map_err(|_| "Cannot prepare the revocation rules.")?;
            let tested = self
                .exec
                .run(binary, &["run", "-test", "-c", &file], &env)
                .map_err(|_| "The revocation rules could not be checked.")?;
            if !tested.success {
                return Err("Xray rejected the revocation rules.".into());
            }
            // AddRule cannot prepend. Its replacement may clear rules before failing,
            // so retry the same complete graph, including every existing revocation.
            for _ in 0..2 {
                if self
                    .exec
                    .run(binary, &["api", "adrules", &server, &file], &env)
                    .is_ok_and(|output| output.success)
                    && self
                        .live_rules(binary, api)
                        .is_ok_and(|rules| rules == listed(&desired["routing"]))
                {
                    return Ok(());
                }
            }
            Err("The Xray routing API could not install the revocation rules.".into())
        })();
        let _ = fs::remove_file(&path);
        result
    }

    fn flow_context<'a>(
        &self,
        resources: &'a [Resource],
        statuses: &HashMap<String, &'static str>,
    ) -> Result<Option<FlowContext<'a>>, ResourceError> {
        let Some(config) = resources.iter().find_map(|resource| match &resource.spec {
            Spec::XrayConfig(config) => Some(config),
            _ => None,
        }) else {
            return Ok(None);
        };
        if tags(resources).is_empty() {
            return Ok(None);
        }
        if !xray_identity::enabled(&config.config) {
            return Err(ResourceError {
                code: "unsupported".into(),
                message: "Apply the fleet to enable Xray's RoutingService.".into(),
                retryable: false,
            });
        }
        let result = (|| {
            for resource in resources {
                let relevant = match &resource.spec {
                    Spec::XrayConfig(_) | Spec::XrayUsers(_) | Spec::Hysteria2Users(_) => true,
                    Spec::BinaryPin(pin) => {
                        matches!(pin.name.as_str(), "xray" | "geoip" | "geosite")
                    }
                    Spec::Unit(unit) => unit.name == crate::xray_health::CORE_UNIT,
                    _ => false,
                };
                if relevant && statuses.get(&resource.id) != Some(&"applied") {
                    return Err("The node has not applied its Xray resources.".to_string());
                }
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
            let installed = fs::read(fsutil::host_path(&self.root, &config.path))
                .map_err(|_| "The composed Xray configuration is not installed.")?;
            if installed != compose::render(&composed) {
                return Err("The installed Xray credentials differ from the held document.".into());
            }
            Ok(FlowContext {
                config,
                binary,
                composed,
            })
        })();
        result.map(Some).map_err(flow_error)
    }

    pub(crate) fn flow_guard_result(
        &self,
        resources: &[Resource],
        statuses: &HashMap<String, &'static str>,
    ) -> Option<ResourceError> {
        let result = self.flow_context(resources, statuses).and_then(|context| {
            let Some(context) = context else {
                return Ok(());
            };
            self.guard_live_users(
                &context.binary,
                &context.config.api_address,
                &context.composed,
                resources,
                None,
            )
            .map_err(|failure| flow_error(failure.message))
        });
        result.err()
    }

    /// Read-only evidence: a fresh core needs no blocks for sessions it already dropped.
    pub(crate) fn flow_guard_observation(
        &self,
        resources: &[Resource],
        statuses: &HashMap<String, &'static str>,
    ) -> Option<ResourceError> {
        let result = self.flow_context(resources, statuses).and_then(|context| {
            let Some(context) = context else {
                return Ok(());
            };
            let result = (|| {
                let invocation = core_invocation(self.exec)?;
                let path = self
                    .cache_dir
                    .parent()
                    .ok_or("The core revocation retry directory is unavailable.")?
                    .join("flow-revocations.json");
                let mut revoked = Revocations::load(&path)?;
                revoked.for_invocation(&invocation);
                let current = self.live_rules(&context.binary, &context.config.api_address)?;
                let existing: Vec<_> = current
                    .iter()
                    .map(|(tag, _)| Revocation::from_rule_tag(tag))
                    .collect::<Result<Vec<_>, _>>()?
                    .into_iter()
                    .flatten()
                    .collect();
                revoked.add(existing, &context.composed)?;
                if revoked.overflow {
                    return Err(LIMIT_MESSAGE.into());
                }
                let desired = guarded(&context.composed, &revoked.identities)?;
                if current != listed(&desired["routing"]) {
                    return Err("The running core has not confirmed its revocation rules.".into());
                }
                if core_invocation(self.exec)? != invocation {
                    return Err(
                        "The core restarted while revocation evidence was being read.".into(),
                    );
                }
                Ok(())
            })();
            result.map_err(flow_error)
        });
        result.err()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_revocation_precedes_every_rule_and_keeps_balancers_and_api_routes() {
        let before = json!({"api": {"services": ["RoutingService"]},
            "inbounds": [{"tag": "hy2", "settings": {"clients": [{"email": "u-a.c11111111111111111111111111111111"}, {"email": "u-b.c22222222222222222222222222222222"}]}}],
            "outbounds": [{"tag": "direct"}, {"tag": "block", "protocol": "blackhole"}],
            "routing": {"balancers": [{"tag": "pool", "selector": ["exit-"]}], "rules": [
                {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
                {"type": "field", "network": "tcp", "balancerTag": "pool"}]}});
        let mut after = before.clone();
        after["inbounds"][0]["settings"]["clients"] =
            json!([{"email": "u-b.c22222222222222222222222222222222"}]);
        let config = guarded(&after, &lost(&after, &["hy2".into()], &before)).unwrap();
        assert_eq!(
            config["routing"]["rules"][0]["user"],
            json!(["u-a.c11111111111111111111111111111111"])
        );
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
        let rebuilt = guarded(&after, &BTreeSet::new()).unwrap();
        assert!(
            !rebuilt
                .to_string()
                .contains("u-a.c11111111111111111111111111111111"),
            "revoked generations must not accumulate"
        );
    }
    #[test]
    fn active_users_leave_domain_requests_unmatched_for_ip_if_non_match() {
        let old = xray_identity::email("u-a", "old-key");
        let active = xray_identity::email("u-b", "active-key");
        let before = json!({
            "api": {"services": ["RoutingService"]},
            "inbounds": [{"tag": "hy2", "settings": {"clients": [
                {"email": old}, {"email": active}
            ]}}],
            "outbounds": [{"tag": "direct", "protocol": "freedom"},
                {"tag": "block", "protocol": "blackhole"}],
            "routing": {"domainStrategy": "IPIfNonMatch", "rules": [
                {"type": "field", "ip": ["geoip:private"], "outboundTag": "block"},
                {"type": "field", "ip": ["198.51.100.0/24"], "outboundTag": "direct"}
            ]}
        });
        let mut after = before.clone();
        after["inbounds"][0]["settings"]["clients"] = json!([{"email": active}]);
        let result = guarded(&after, &lost(&after, &["hy2".into()], &before)).unwrap();
        let rules = result["routing"]["rules"].as_array().unwrap();
        assert_eq!(
            rules.len(),
            3,
            "an active-user rule prevents the DNS routing pass"
        );
        assert_eq!(&rules[1..], before["routing"]["rules"].as_array().unwrap());
        assert!(
            rules[0]["user"]
                .as_array()
                .unwrap()
                .iter()
                .all(|user| user != &active)
        );
    }
}
