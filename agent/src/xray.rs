//! Applying the Xray config and its managed users.
//!
//! The composed config (users filled into their inbounds) is validated with
//! `xray run -test` and written to disk, which stays the source of truth after
//! a restart. When only users changed and Xray is running the previous
//! composition, the change goes through Xray's own API without a restart:
//! VLESS users through `xray api rmu` / `adu`; Hysteria2 removals through `rmu`
//! (the hysteria inbound removes by email), and Hysteria2 additions by
//! rebuilding only that inbound with `rmi` + `adi`, because `adu` cannot build
//! hysteria users (Xray 26.7.28 answers "unsupported inbound type") although the
//! inbound itself accepts them over gRPC. A rebuild drops open Hysteria2 sessions
//! on that inbound (clients reconnect) and nothing else. Anything more (a config
//! change, an API failure) restarts Xray through the unit's `restart_on`.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::fs;
use std::io::Write;
use std::path::Path;

use serde_json::{Value, json};

use crate::apply::{Engine, Failure, Outcome, last_line, truncate, unrecorded};
use crate::compose::{self, UsersRef};
use crate::fsutil::{self, host_path};
use crate::model::{Hysteria2UsersSpec, Resource, Spec, XrayConfigSpec, XrayUsersSpec};
use crate::state::LedgerEntry;
use crate::xray_live_apply::LiveApply;
use crate::{debug, info};

/// Users of one inbound to change through the API.
#[derive(Debug, PartialEq)]
pub struct LiveChange {
    pub tag: String,
    /// Emails (user IDs) to remove: users gone or whose credentials changed.
    pub remove: Vec<String>,
    /// VLESS: the inbound object from the composed config with only the users to add as clients.
    pub add: Option<Value>,
    /// Hysteria2 with users to add: the whole composed inbound, removed and added back
    /// (`remove` and `add` are then empty: the rebuilt inbound carries every user).
    pub rebuild: Option<Value>,
    /// Typed Hysteria2 account additions with generation identities.
    pub hysteria2: bool,
}

impl LiveChange {
    fn add_count(&self) -> usize {
        self.add
            .as_ref()
            .and_then(|inbound| inbound.pointer("/settings/clients"))
            .and_then(Value::as_array)
            .map_or(0, Vec::len)
    }
}

fn vless_clients(spec: &XrayUsersSpec) -> BTreeMap<&str, (&str, &str)> {
    spec.users
        .iter()
        .map(|user| {
            (
                user.user_id.as_str(),
                (user.uuid.as_str(), user.flow.as_str()),
            )
        })
        .collect()
}

pub(crate) fn composed_inbound(composed: &Value, tag: &str) -> Result<Value, String> {
    composed
        .get("inbounds")
        .and_then(Value::as_array)
        .and_then(|items| {
            items
                .iter()
                .find(|i| i.get("tag").and_then(Value::as_str) == Some(tag))
        })
        .cloned()
        .ok_or_else(|| "the composed config lost the inbound".to_string())
}

/// The API changes that turn `previous` into `current` for one Hysteria2 inbound of `composed`:
/// removals alone go through `rmu`; any addition or new credential rebuilds the inbound.
pub fn diff_hysteria2_users(
    previous: &Hysteria2UsersSpec,
    current: &Hysteria2UsersSpec,
    composed: &Value,
) -> Result<LiveChange, String> {
    let auth = |spec: &Hysteria2UsersSpec| -> BTreeMap<String, String> {
        spec.users
            .iter()
            .map(|user| (user.user_id.clone(), user.auth.clone()))
            .collect()
    };
    let (before, after) = (auth(previous), auth(current));
    let adds = after.iter().any(|(id, key)| before.get(id) != Some(key));
    let generations = crate::xray_identity::enabled(composed);
    if adds && !generations {
        return Ok(LiveChange {
            tag: current.inbound_tag.clone(),
            remove: Vec::new(),
            add: None,
            rebuild: Some(composed_inbound(composed, &current.inbound_tag)?),
            hysteria2: false,
        });
    }
    let email = |id: &str, auth: &str| {
        if generations {
            crate::xray_identity::email(id, auth)
        } else {
            id.to_string()
        }
    };
    let added: BTreeSet<String> = after
        .iter()
        .filter(|(id, key)| before.get(*id) != Some(*key))
        .map(|(id, auth)| email(id, auth))
        .collect();
    let add = if added.is_empty() {
        None
    } else {
        let mut inbound = composed_inbound(composed, &current.inbound_tag)?;
        let clients = inbound
            .pointer_mut("/settings/clients")
            .and_then(Value::as_array_mut)
            .ok_or("the inbound has no clients list")?;
        clients.retain(|client| {
            client
                .get("email")
                .and_then(Value::as_str)
                .is_some_and(|email| added.contains(email))
        });
        Some(inbound)
    };
    Ok(LiveChange {
        tag: current.inbound_tag.clone(),
        remove: before
            .iter()
            .filter(|(id, key)| after.get(*id) != Some(*key))
            .map(|(id, auth)| email(id, auth))
            .collect(),
        add,
        rebuild: None,
        hysteria2: generations,
    })
}

/// The API changes that turn `previous` into `current` for one inbound of `composed`.
pub fn diff_users(
    previous: &XrayUsersSpec,
    current: &XrayUsersSpec,
    composed: &Value,
) -> Result<LiveChange, String> {
    let before = vless_clients(previous);
    let after = vless_clients(current);
    let email = |id: &str, uuid: &str| {
        if crate::xray_identity::enabled(composed) {
            crate::xray_identity::email(id, uuid)
        } else {
            id.to_string()
        }
    };
    let remove: Vec<String> = before
        .iter()
        .filter(|(id, creds)| after.get(*id) != Some(creds))
        .map(|(id, (uuid, _))| email(id, uuid))
        .collect();
    let added: BTreeSet<String> = after
        .iter()
        .filter(|(id, creds)| before.get(*id) != Some(creds))
        .map(|(id, (uuid, _))| email(id, uuid))
        .collect();
    let add = if added.is_empty() {
        None
    } else {
        let mut inbound = composed_inbound(composed, &current.inbound_tag)?;
        let clients = inbound
            .pointer_mut("/settings/clients")
            .and_then(Value::as_array_mut)
            .ok_or("the inbound has no clients list")?;
        clients.retain(|client| {
            client
                .get("email")
                .and_then(Value::as_str)
                .is_some_and(|email| added.contains(email))
        });
        Some(inbound)
    };
    Ok(LiveChange {
        tag: current.inbound_tag.clone(),
        remove,
        add,
        rebuild: None,
        hysteria2: false,
    })
}

impl Engine<'_> {
    pub(crate) fn apply_xray_config(
        &self,
        spec: &XrayConfigSpec,
        resources: &[Resource],
        statuses: &HashMap<String, &'static str>,
        entries: &HashMap<String, LedgerEntry>,
        changed: &mut BTreeSet<String>,
        placed: &mut HashSet<String>,
    ) -> Outcome {
        match self.try_xray_config(spec, resources, statuses, entries, changed, placed) {
            Ok(outcome) => outcome,
            Err(failure) => Outcome::Failed(failure),
        }
    }

    fn try_xray_config(
        &self,
        spec: &XrayConfigSpec,
        resources: &[Resource],
        statuses: &HashMap<String, &'static str>,
        entries: &HashMap<String, LedgerEntry>,
        changed: &mut BTreeSet<String>,
        placed: &mut HashSet<String>,
    ) -> Result<Outcome, Failure> {
        fsutil::check_path(&spec.path).map_err(|msg| Failure::new("path_not_allowed", msg))?;
        let users: Vec<(UsersRef<'_>, &Resource)> = resources
            .iter()
            .filter_map(|r| match &r.spec {
                Spec::XrayUsers(s) => Some((UsersRef::Vless(s), r)),
                Spec::Hysteria2Users(s) => Some((UsersRef::Hysteria2(s), r)),
                _ => None,
            })
            .collect();
        let refs: Vec<UsersRef<'_>> = users.iter().map(|(u, _)| *u).collect();
        let composed = compose::compose(&spec.config, &refs)
            .map_err(|msg| Failure::new("validation_failed", msg))?;
        let bytes = compose::render(&composed);
        let installed = Some(crate::jcs::sha256_hex(&bytes));
        let dest = host_path(&self.root, &spec.path);
        let current =
            fsutil::read_optional(&dest).map_err(|err| Failure::io(&err, "cannot read config"))?;
        if current.as_deref() == Some(&bytes[..]) {
            fsutil::ensure_mode_owner(&dest, 0o600, None)
                .map_err(|err| Failure::io(&err, "cannot set mode"))?;
            placed.extend(users.iter().map(|(u, _)| u.id().to_string()));
            let entry = entries.get(&spec.id).filter(|e| e.kind == "xray_config");
            return Ok(Outcome::Applied {
                changed: unrecorded(entry, &installed),
                installed,
            });
        }

        let pin = |name: &str| {
            resources.iter().find_map(|r| match &r.spec {
                Spec::BinaryPin(p) if p.name == name => Some((p, r.id.as_str())),
                _ => None,
            })
        };
        let (xray, xray_id) = pin("xray").ok_or_else(|| {
            Failure::new("validation_failed", "the document has no xray binary_pin")
        })?;
        if statuses.get(xray_id) != Some(&"applied") {
            return Err(Failure::new(
                "validation_failed",
                "the xray binary is not installed",
            ));
        }
        let asset_dir = pin("geosite").or_else(|| pin("geoip")).and_then(|(p, _)| {
            host_path(&self.root, &p.install_path)
                .parent()
                .map(Path::to_path_buf)
        });

        let (tmp, mut file) = fsutil::create_temp(&dest, "netagent-test.json")
            .map_err(|err| Failure::io(&err, "cannot write config"))?;
        let written = file.write_all(&bytes).and_then(|()| file.sync_all());
        drop(file);
        if let Err(err) = written {
            let _ = fs::remove_file(&tmp);
            return Err(Failure::io(&err, "cannot write config"));
        }
        let binary = host_path(&self.root, &xray.install_path)
            .to_string_lossy()
            .into_owned();
        let tmp_arg = tmp.to_string_lossy().into_owned();
        let asset = asset_dir.map(|dir| dir.to_string_lossy().into_owned());
        let env: Vec<(&str, &str)> = asset
            .iter()
            .map(|dir| ("XRAY_LOCATION_ASSET", dir.as_str()))
            .collect();
        let tested = self
            .exec
            .run(&binary, &["run", "-test", "-c", &tmp_arg], &env);
        let failure = match tested {
            Ok(output) if output.success => None,
            Ok(output) => Some(format!(
                "xray rejected the config: {}",
                last_line(&output.stdout, &output.stderr)
            )),
            Err(err) => Some(format!("cannot run xray: {err}")),
        };
        if let Some(message) = failure {
            let _ = fs::remove_file(&tmp);
            return Err(Failure::new("validation_failed", truncate(&message, 300)));
        }

        let live = self.live_plan(spec, resources, entries, current.as_deref(), &composed);
        let live_done = if let Some(changes) = live {
            let previous: Value =
                serde_json::from_slice(current.as_ref().expect("live plan checked the file"))
                    .map_err(|_| {
                        Failure::new("validation_failed", "The previous Xray config is invalid.")
                    })?;
            self.apply_live_config(LiveApply {
                spec,
                resources,
                composed: &composed,
                binary: &binary,
                previous: &previous,
                changes: &changes,
                temporary: &tmp,
            })?
        } else {
            self.keep_previous(&dest);
            fsutil::commit(&tmp, &dest, 0o600, None)
                .map_err(|err| Failure::io(&err, "cannot install config"))?;
            false
        };
        placed.extend(users.iter().map(|(u, _)| u.id().to_string()));
        if live_done {
            info!("changed xray users through the api; wrote {}", spec.path);
            return Ok(Outcome::Applied {
                changed: false,
                installed,
            });
        }
        info!("wrote xray config {}", spec.path);
        for (_, resource) in &users {
            let same = entries.get(&resource.id).is_some_and(|entry| {
                entry.kind == resource.kind && entry.applied_sha256 == resource.hash
            });
            if !same {
                changed.insert(resource.id.clone());
            }
        }
        Ok(Outcome::Applied {
            changed: true,
            installed,
        })
    }

    /// The API changes that bring a running Xray to the new users, or `None` when only a
    /// restart will do: the config resource changed, the file on disk is not the previous
    /// composition, a users resource is new or moved to another inbound, or Xray is not running.
    fn live_plan(
        &self,
        spec: &XrayConfigSpec,
        resources: &[Resource],
        entries: &HashMap<String, LedgerEntry>,
        current: Option<&[u8]>,
        composed: &Value,
    ) -> Option<Vec<LiveChange>> {
        let config = resources.iter().find(|r| r.id == spec.id)?;
        let config_entry = entries.get(&config.id)?;
        if config_entry.kind != config.kind || config_entry.applied_sha256 != config.hash {
            return None;
        }
        let mut previous: Vec<(&Resource, Resource)> = Vec::new();
        for resource in resources {
            if !matches!(resource.spec, Spec::XrayUsers(_) | Spec::Hysteria2Users(_)) {
                continue;
            }
            let entry = entries
                .get(&resource.id)
                .filter(|e| e.kind == resource.kind)?;
            previous.push((resource, Resource::parse(&entry.resource).ok()?));
        }
        let previous_refs: Vec<UsersRef<'_>> = previous
            .iter()
            .filter_map(|(_, old)| match &old.spec {
                Spec::XrayUsers(s) => Some(UsersRef::Vless(s)),
                Spec::Hysteria2Users(s) => Some(UsersRef::Hysteria2(s)),
                _ => None,
            })
            .collect();
        if previous_refs.len() != previous.len() {
            return None;
        }
        let before = compose::render(&compose::compose(&spec.config, &previous_refs).ok()?);
        if current != Some(&before[..]) {
            return None;
        }
        let mut changes = Vec::new();
        for (resource, old) in &previous {
            if old.hash == resource.hash {
                continue;
            }
            match (&old.spec, &resource.spec) {
                (Spec::XrayUsers(old), Spec::XrayUsers(new))
                    if old.inbound_tag == new.inbound_tag =>
                {
                    changes.push(diff_users(old, new, composed).ok()?);
                }
                (Spec::Hysteria2Users(old), Spec::Hysteria2Users(new))
                    if old.inbound_tag == new.inbound_tag =>
                {
                    changes.push(diff_hysteria2_users(old, new, composed).ok()?);
                }
                _ => return None,
            }
        }
        if changes.is_empty() || !self.xray_running(resources) {
            return None;
        }
        Some(changes)
    }

    /// Every unit that restarts on a users resource is active.
    fn xray_running(&self, resources: &[Resource]) -> bool {
        let users: HashSet<&str> = resources
            .iter()
            .filter(|r| matches!(r.spec, Spec::XrayUsers(_) | Spec::Hysteria2Users(_)))
            .map(|r| r.id.as_str())
            .collect();
        let units: Vec<&str> = resources
            .iter()
            .filter_map(|r| match &r.spec {
                Spec::Unit(unit)
                    if unit.restart_on.iter().any(|id| users.contains(id.as_str())) =>
                {
                    Some(unit.name.as_str())
                }
                _ => None,
            })
            .collect();
        !units.is_empty()
            && units
                .iter()
                .all(|name| self.query("is-active", name) == "active")
    }

    /// Remove, then add, users through `xray api`; any shortfall is an error (the caller restarts).
    pub(crate) fn apply_live(
        &self,
        binary: &str,
        api: &str,
        changes: &[LiveChange],
    ) -> Result<(), String> {
        let server = format!("--server={api}");
        for change in changes {
            if let Some(inbound) = &change.rebuild {
                self.rebuild_inbound(binary, &server, &change.tag, inbound)?;
                debug!("xray api: rebuilt inbound {}", change.tag);
                continue;
            }
            if !change.remove.is_empty() {
                let tag = format!("-tag={}", change.tag);
                let mut args = vec!["api", "rmu", server.as_str(), tag.as_str()];
                args.extend(change.remove.iter().map(String::as_str));
                let output = self.xray_api(binary, &args)?;
                expect_count(&output, "Removed", change.remove.len())?;
            }
            if let Some(inbound) = &change.add {
                if change.hysteria2 {
                    for client in inbound
                        .pointer("/settings/clients")
                        .and_then(Value::as_array)
                        .into_iter()
                        .flatten()
                    {
                        let email = client
                            .get("email")
                            .and_then(Value::as_str)
                            .ok_or("the Hysteria2 user has no email")?;
                        let auth = client
                            .get("auth")
                            .and_then(Value::as_str)
                            .ok_or("the Hysteria2 user has no auth")?;
                        self.add_hysteria_user(api, &change.tag, email, auth)?;
                    }
                    continue;
                }
                fs::create_dir_all(&self.cache_dir).map_err(|err| err.to_string())?;
                // Holds the inbound's keys and the new UUIDs: private, and removed at once.
                let path = self.cache_dir.join(format!("adu-{}.json", change.tag));
                let body = serde_json::to_vec(&json!({"inbounds": [inbound]}))
                    .map_err(|err| err.to_string())?;
                fsutil::write_atomic(&path, &body, 0o600, None).map_err(|err| err.to_string())?;
                let file = path.to_string_lossy().into_owned();
                let ran = self.xray_api(binary, &["api", "adu", server.as_str(), file.as_str()]);
                let _ = fs::remove_file(&path);
                expect_count(&ran?, "Added", change.add_count())?;
            }
            debug!(
                "xray api: inbound {} -{} +{}",
                change.tag,
                change.remove.len(),
                change.add_count()
            );
        }
        Ok(())
    }

    /// Remove one inbound and add it back with all its users (`rmi` + `adi` exit 1 on failure).
    /// A failed `adi` leaves the inbound missing; the caller's restart brings it back from the file.
    pub(crate) fn rebuild_inbound(
        &self,
        binary: &str,
        server: &str,
        tag: &str,
        inbound: &Value,
    ) -> Result<(), String> {
        fs::create_dir_all(&self.cache_dir).map_err(|err| err.to_string())?;
        // Holds the users' auth and the obfuscation password: private, and removed at once.
        let path = self.cache_dir.join(format!("adi-{tag}.json"));
        let body =
            serde_json::to_vec(&json!({"inbounds": [inbound]})).map_err(|err| err.to_string())?;
        fsutil::write_atomic(&path, &body, 0o600, None).map_err(|err| err.to_string())?;
        let file = path.to_string_lossy().into_owned();
        let removed = self.xray_api(binary, &["api", "rmi", server, tag]);
        let added = removed.and_then(|_| self.xray_api(binary, &["api", "adi", server, &file]));
        let _ = fs::remove_file(&path);
        added.map(|_| ())
    }

    pub(crate) fn xray_api(&self, binary: &str, args: &[&str]) -> Result<String, String> {
        match self.exec.run(binary, args, &[]) {
            Ok(output) if output.success => Ok(output.stdout),
            Ok(output) => Err(format!(
                "{} {} failed: {}",
                args[0],
                args[1],
                truncate(last_line(&output.stdout, &output.stderr), 200)
            )),
            Err(err) => Err(format!("cannot run xray: {err}")),
        }
    }
}

/// `xray api adu`/`rmu` exit 0 even when a user fails; only their summary line tells.
fn expect_count(output: &str, verb: &str, expected: usize) -> Result<(), String> {
    let done = output.lines().rev().find_map(|line| {
        line.trim()
            .strip_prefix(verb)?
            .split_whitespace()
            .next()?
            .parse::<usize>()
            .ok()
    });
    match done {
        Some(count) if count == expected => Ok(()),
        Some(count) => Err(format!(
            "{} {count} of {expected} user(s)",
            verb.to_lowercase()
        )),
        None => Err(format!(
            "no summary from xray api ({})",
            verb.to_lowercase()
        )),
    }
}

/// Users resources are applied once the composed config that carries them is in place.
pub(crate) fn users_outcome(
    resource: &Resource,
    resources: &[Resource],
    config_seen: bool,
    statuses: &HashMap<String, &'static str>,
    placed: &HashSet<String>,
    changed: &BTreeSet<String>,
) -> Outcome {
    let config = resources
        .iter()
        .find(|r| matches!(r.spec, Spec::XrayConfig(_)));
    let Some(config) = config else {
        return Outcome::Failed(Failure::new(
            "validation_failed",
            "the document has no xray_config",
        ));
    };
    if !config_seen {
        return Outcome::Failed(Failure::new(
            "validation_failed",
            "users resources must follow xray_config",
        ));
    }
    if placed.contains(&resource.id) {
        Outcome::Applied {
            changed: changed.contains(&resource.id),
            installed: None,
        }
    } else if statuses.get(&config.id) == Some(&"applied") {
        Outcome::Failed(Failure::new(
            "internal",
            "users were not composed into the config",
        ))
    } else {
        Outcome::Blocked
    }
}

#[cfg(test)]
mod tests {
    use super::{diff_hysteria2_users, expect_count};
    use crate::model::{Hysteria2User, Hysteria2UsersSpec};
    use serde_json::json;

    fn hy2(users: &[(&str, &str)]) -> Hysteria2UsersSpec {
        Hysteria2UsersSpec {
            id: "users:hy2".into(),
            kind: "hysteria2_users".into(),
            depends_on: Vec::new(),
            inbound_tag: "hy2".into(),
            users: users
                .iter()
                .map(|(id, auth)| Hysteria2User {
                    user_id: (*id).into(),
                    auth: (*auth).into(),
                })
                .collect(),
        }
    }

    #[test]
    fn hysteria2_removals_use_rmu_and_any_new_credential_rebuilds_the_inbound() {
        let composed = json!({"inbounds": [{"tag": "hy2", "settings": {"clients": []}}]});
        let before = hy2(&[("u-a", "k1"), ("u-b", "k2")]);
        let removal = diff_hysteria2_users(&before, &hy2(&[("u-b", "k2")]), &composed).unwrap();
        assert_eq!(removal.remove, ["u-a"]);
        assert!(removal.rebuild.is_none() && removal.add.is_none());
        // A changed key cannot be removed and re-added: adu has no hysteria case.
        let rekeyed = diff_hysteria2_users(&before, &hy2(&[("u-a", "k3")]), &composed).unwrap();
        assert!(rekeyed.remove.is_empty());
        assert_eq!(rekeyed.rebuild.unwrap()["tag"], "hy2");
    }

    #[test]
    fn api_summaries_must_count_every_user() {
        let out = "processing inbound: a\nadd user: u1\nresult: ok\nAdded 1 user(s) in total.\n";
        assert!(expect_count(out, "Added", 1).is_ok());
        assert!(expect_count(out, "Added", 2).is_err());
        assert!(expect_count("Removed 0 user(s) in total.", "Removed", 1).is_err());
        assert!(expect_count("", "Removed", 1).is_err());
    }
}
