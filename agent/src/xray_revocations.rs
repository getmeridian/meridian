//! Revocation intent survives agent retries, but only within one systemd core invocation.
//! Routing rules themselves are never written into core.json.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::time::Duration;

use base64::Engine as _;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::fsutil;
use crate::model::{check_tag, check_user_id};
use crate::system::Exec;
use crate::xray_health::CORE_UNIT;
use crate::xray_identity;

pub(crate) const RULE_PREFIX: &str = "meridian-revoked-";
pub(crate) const MAX_REVOCATIONS: usize = 4096;
const MAX_LEDGER_BYTES: usize = 2 * 1024 * 1024;
pub(crate) const LIMIT_MESSAGE: &str = "The core reached its limit of 4096 revocation rules. Restart it to finish blocking new streams.";

#[derive(Clone, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Revocation {
    pub tag: String,
    pub email: String,
}

impl Revocation {
    pub fn valid(&self) -> bool {
        let person = xray_identity::person_id(&self.email);
        check_tag(&self.tag).is_ok()
            && check_user_id(person).is_ok()
            && (person == self.email
                || self.email.strip_prefix(person).is_some_and(|suffix| {
                    suffix.len() == 34
                        && suffix.starts_with(".c")
                        && suffix[2..].bytes().all(|byte| byte.is_ascii_hexdigit())
                }))
    }

    pub fn rule_tag(&self) -> String {
        let bytes = serde_json::to_vec(self).expect("revocation strings serialize");
        format!("{RULE_PREFIX}{}", URL_SAFE_NO_PAD.encode(bytes))
    }

    pub fn from_rule_tag(tag: &str) -> Result<Option<Self>, String> {
        let Some(encoded) = tag.strip_prefix(RULE_PREFIX) else {
            return Ok(None);
        };
        let value = (|| {
            if encoded.len() > 512 {
                return None;
            }
            let bytes = URL_SAFE_NO_PAD.decode(encoded).ok()?;
            let item: Self = serde_json::from_slice(&bytes).ok()?;
            item.valid().then_some(item)
        })();
        value
            .map(Some)
            .ok_or_else(|| "The core returned an invalid revocation rule.".into())
    }
}

pub(crate) fn clients(config: &Value, tag: &str) -> BTreeSet<String> {
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
        .map(str::to_ascii_lowercase)
        .collect()
}

pub(crate) fn lost(config: &Value, tags: &[String], previous: &Value) -> BTreeSet<Revocation> {
    let mut result = BTreeSet::new();
    for tag in tags {
        let active = clients(config, tag);
        for email in clients(previous, tag).difference(&active) {
            result.insert(Revocation {
                tag: tag.clone(),
                email: email.clone(),
            });
        }
    }
    result
}

pub(crate) fn active_identities<'a>(
    config: &Value,
    identities: impl IntoIterator<Item = &'a Revocation>,
) -> BTreeSet<Revocation> {
    let mut active = BTreeMap::new();
    identities
        .into_iter()
        .filter(|identity| {
            active
                .entry(identity.tag.clone())
                .or_insert_with(|| clients(config, &identity.tag))
                .contains(&identity.email)
        })
        .cloned()
        .collect()
}

#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Revocations {
    pub invocation: String,
    pub identities: BTreeSet<Revocation>,
    pub overflow: bool,
}

impl Revocations {
    pub fn load(path: &Path) -> Result<Self, String> {
        let bytes = fsutil::read_optional(path)
            .map_err(|_| "Cannot read the core revocation retry ledger.")?;
        let Some(bytes) = bytes else {
            return Ok(Self::default());
        };
        if bytes.len() > MAX_LEDGER_BYTES {
            return Err("The core revocation retry ledger is too large.".into());
        }
        let value: Self = serde_json::from_slice(&bytes)
            .map_err(|_| "The core revocation retry ledger is invalid.")?;
        if !valid_invocation(&value.invocation)
            || value.identities.len() > MAX_REVOCATIONS
            || value.identities.iter().any(|item| !item.valid())
        {
            return Err("The core revocation retry ledger is invalid.".into());
        }
        Ok(value)
    }

    pub fn add(
        &mut self,
        identities: impl IntoIterator<Item = Revocation>,
        config: &Value,
    ) -> Result<(), String> {
        let incoming: Vec<_> = identities.into_iter().collect();
        let active = active_identities(config, self.identities.iter().chain(&incoming));
        self.identities
            .retain(|identity| !active.contains(identity));
        for identity in incoming {
            if !identity.valid() {
                return Err("The revoked Xray identity is invalid.".into());
            }
            if active.contains(&identity) || self.identities.contains(&identity) {
                continue;
            }
            if self.identities.len() == MAX_REVOCATIONS {
                self.overflow = true;
            } else {
                self.identities.insert(identity);
            }
        }
        Ok(())
    }

    pub fn save_if_changed(&self, path: &Path, before: &Self) -> Result<(), String> {
        if self == before {
            return Ok(());
        }
        let bytes = serde_json::to_vec(self)
            .map_err(|_| "Cannot prepare the core revocation retry ledger.")?;
        fsutil::write_atomic(path, &bytes, 0o600, None)
            .map_err(|_| "Cannot save the core revocation retry ledger.".into())
    }

    pub fn for_invocation(&mut self, invocation: &str) {
        if self.invocation != invocation {
            self.invocation = invocation.to_string();
            self.identities.clear();
            self.overflow = false;
        }
    }
}

fn valid_invocation(value: &str) -> bool {
    value.len() == 32 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

pub(crate) fn core_invocation(exec: &dyn Exec) -> Result<String, String> {
    let output = exec
        .run_timeout(
            "systemctl",
            &["show", CORE_UNIT, "--property=ActiveState,InvocationID"],
            &[],
            Duration::from_secs(1),
        )
        .map_err(|_| "The running core instance could not be checked.")?;
    if !output.success {
        return Err("The running core instance could not be checked.".into());
    }
    let field = |key: &str| {
        output
            .stdout
            .lines()
            .find_map(|line| line.strip_prefix(key))
    };
    if !matches!(
        field("ActiveState="),
        Some("active" | "activating" | "reloading")
    ) {
        return Err("The core is not running.".into());
    }
    field("InvocationID=")
        .filter(|value| valid_invocation(value))
        .map(str::to_string)
        .ok_or_else(|| "The running core instance has no invocation ID.".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn revocations_are_bounded_pruned_on_reenable_and_cleared_on_restart() {
        let mut ledger = Revocations::default();
        ledger.for_invocation(&"1".repeat(32));
        let identities = (0..=MAX_REVOCATIONS).map(|generation| Revocation {
            tag: "hy2".into(),
            email: xray_identity::email("u-a", &generation.to_string()),
        });
        ledger.add(identities, &json!({})).unwrap();
        assert_eq!(ledger.identities.len(), MAX_REVOCATIONS);
        assert!(ledger.overflow);
        let email = ledger.identities.first().unwrap().email.clone();
        ledger
            .add(
                [],
                &json!({"inbounds": [{"tag": "hy2", "settings": {
                    "clients": [{"email": email}]
                }}]}),
            )
            .unwrap();
        assert_eq!(ledger.identities.len(), MAX_REVOCATIONS - 1);
        ledger.for_invocation(&"2".repeat(32));
        assert!(ledger.identities.is_empty() && !ledger.overflow);
    }

    #[test]
    fn a_running_rule_carries_its_validated_identity_for_agent_recovery() {
        let identity = Revocation {
            tag: "vless".into(),
            email: xray_identity::email("u-a", "old-key"),
        };
        assert_eq!(
            Revocation::from_rule_tag(&identity.rule_tag()).unwrap(),
            Some(identity)
        );
        assert!(Revocation::from_rule_tag("meridian-revoked-!!!").is_err());
        assert!(
            Revocation::from_rule_tag("ordinary-rule")
                .unwrap()
                .is_none()
        );
        assert!(
            !Revocation {
                tag: "../core".into(),
                email: "u-a".into()
            }
            .valid()
        );
    }
}
