//! Per-user traffic counters: Xray's resettable counters folded into a durable store.
//!
//! Each round reads and resets Xray's `user>>>…>>>traffic>>>…` counters with
//! `xray api statsquery -reset`, adds them to `traffic.json` (0600), and sends
//! the cumulative totals under the store's `counter_epoch` and the next `seq`.
//! Totals survive Xray and agent restarts; losing the file draws a new epoch,
//! which the service reads as every counter starting from zero. Traffic that
//! arrives between a reset and the store's save is lost if the agent dies in
//! between — a bounded undercount, never a double count.

use std::collections::{BTreeMap, BTreeSet};
use std::io::ErrorKind;
use std::path::Path;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::identity::random_uuid;
use crate::model::{StatsAck, StatsReport, UserTraffic};
use crate::system::Exec;

pub const TRAFFIC_FILE: &str = "traffic.json";
/// The contract's `Count` ceiling (2^53 − 1): totals saturate there.
const MAX_COUNT: u64 = (1 << 53) - 1;
const MAX_USERS: usize = 10_000;

#[derive(Serialize, Deserialize, Clone, Copy, Debug, Default, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Totals {
    pub uplink: u64,
    pub downlink: u64,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct TrafficStore {
    pub counter_epoch: String,
    /// The last `seq` handed out; each send attempt takes a new one.
    pub seq: u64,
    pub users: BTreeMap<String, Totals>,
}

impl TrafficStore {
    pub fn new() -> Result<TrafficStore, String> {
        Ok(TrafficStore {
            counter_epoch: random_uuid()?,
            seq: 0,
            users: BTreeMap::new(),
        })
    }

    /// Load the store; a missing or unreadable one starts a new epoch (the service then
    /// counts from zero, which never double counts).
    pub fn load(state_dir: &Path) -> Result<TrafficStore, String> {
        let path = state_dir.join(TRAFFIC_FILE);
        match std::fs::read(&path) {
            Ok(bytes) => match serde_json::from_slice::<TrafficStore>(&bytes) {
                Ok(store) if store.seq < MAX_COUNT => Ok(store),
                _ => {
                    crate::info!(
                        "{} is malformed; starting new traffic counters",
                        path.display()
                    );
                    TrafficStore::new()
                }
            },
            Err(err) if err.kind() == ErrorKind::NotFound => TrafficStore::new(),
            Err(err) => Err(format!("cannot read {}: {err}", path.display())),
        }
    }

    pub fn save(&self, state_dir: &Path) -> Result<(), String> {
        let bytes = serde_json::to_vec(self).map_err(|err| err.to_string())?;
        std::fs::create_dir_all(state_dir)
            .map_err(|err| format!("cannot create state dir: {err}"))?;
        crate::fsutil::write_atomic(&state_dir.join(TRAFFIC_FILE), &bytes, 0o600, None)
            .map_err(|err| format!("cannot save traffic counters: {err}"))
    }

    /// Add one round of reset counters.
    pub fn fold(&mut self, deltas: &BTreeMap<String, Totals>) {
        for (user, delta) in deltas {
            if !self.users.contains_key(user) && self.users.len() >= MAX_USERS {
                continue;
            }
            let total = self.users.entry(user.clone()).or_default();
            total.uplink = total.uplink.saturating_add(delta.uplink).min(MAX_COUNT);
            total.downlink = total.downlink.saturating_add(delta.downlink).min(MAX_COUNT);
        }
    }

    /// The next report: every stored total under a fresh `seq`. Save before sending, so a
    /// `seq` is never reused for different totals.
    pub fn next_report(&mut self, collected_at: String) -> StatsReport {
        self.seq += 1;
        StatsReport {
            kind: "stats",
            counter_epoch: self.counter_epoch.clone(),
            seq: self.seq,
            collected_at,
            users: self
                .users
                .iter()
                .map(|(user, total)| UserTraffic {
                    user_id: user.clone(),
                    uplink_bytes: total.uplink,
                    downlink_bytes: total.downlink,
                })
                .collect(),
        }
    }

    /// The service counted everything up to the latest report: forget users no longer managed
    /// (that report held their final totals). Returns whether the store changed.
    pub fn acknowledged(&mut self, ack: &StatsAck, managed: &BTreeSet<String>) -> bool {
        if ack.kind != "stats_ack"
            || ack.counter_epoch != self.counter_epoch
            || ack.acked_seq != self.seq
        {
            return false;
        }
        let before = self.users.len();
        self.users.retain(|user, _| managed.contains(user));
        self.users.len() != before
    }
}

/// Read and reset Xray's per-user counters over its API.
pub fn query(exec: &dyn Exec, xray: &str, api: &str) -> Result<BTreeMap<String, Totals>, String> {
    let server = format!("--server={api}");
    let output = exec
        .run(
            xray,
            &[
                "api",
                "statsquery",
                &server,
                "-pattern",
                "user>>>",
                "-reset",
            ],
            &[],
        )
        .map_err(|err| format!("cannot run xray: {err}"))?;
    if !output.success {
        return Err(format!(
            "statsquery failed: {}",
            crate::apply::truncate(crate::apply::last_line(&output.stdout, &output.stderr), 200)
        ));
    }
    parse_statsquery(&output.stdout)
}

/// Parse `statsquery` JSON: `{"stat":[{"name":"user>>>ID>>>traffic>>>uplink","value":N}]}`.
/// Protobuf JSON writes int64 as a string and omits zero values and an empty list.
pub fn parse_statsquery(stdout: &str) -> Result<BTreeMap<String, Totals>, String> {
    let parsed: Value = serde_json::from_str(stdout.trim())
        .map_err(|err| format!("statsquery output is not JSON: {err}"))?;
    let stats = match parsed.get("stat") {
        None | Some(Value::Null) => return Ok(BTreeMap::new()),
        Some(Value::Array(items)) => items,
        Some(_) => return Err("statsquery output has a malformed stat list".into()),
    };
    let mut totals: BTreeMap<String, Totals> = BTreeMap::new();
    for item in stats {
        let Some(name) = item.get("name").and_then(Value::as_str) else {
            continue;
        };
        let parts: Vec<&str> = name.split(">>>").collect();
        let ["user", user, "traffic", direction] = parts[..] else {
            continue;
        };
        if !is_user_id(user) {
            continue;
        }
        let value = match item.get("value") {
            None | Some(Value::Null) => 0,
            Some(Value::Number(n)) => n.as_u64().ok_or("statsquery value is not a count")?,
            Some(Value::String(s)) => s.parse().map_err(|_| "statsquery value is not a count")?,
            Some(_) => return Err("statsquery value is not a count".into()),
        };
        let entry = totals.entry(user.to_string()).or_default();
        match direction {
            "uplink" => entry.uplink = entry.uplink.saturating_add(value),
            "downlink" => entry.downlink = entry.downlink.saturating_add(value),
            _ => {}
        }
    }
    Ok(totals)
}

/// The contract's `UserId`: `^[a-z0-9][a-z0-9_-]*$`, at most 64 characters.
fn is_user_id(text: &str) -> bool {
    (1..=64).contains(&text.len())
        && text.bytes().enumerate().all(|(i, b)| {
            b.is_ascii_lowercase() || b.is_ascii_digit() || (i > 0 && (b == b'_' || b == b'-'))
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn deltas(items: &[(&str, u64, u64)]) -> BTreeMap<String, Totals> {
        items
            .iter()
            .map(|(u, up, down)| {
                (
                    u.to_string(),
                    Totals {
                        uplink: *up,
                        downlink: *down,
                    },
                )
            })
            .collect()
    }

    #[test]
    fn parses_string_and_number_values_and_skips_foreign_counters() {
        let out = r#"{"stat":[
            {"name":"user>>>u-1>>>traffic>>>uplink","value":"1200"},
            {"name":"user>>>u-1>>>traffic>>>downlink","value":3400},
            {"name":"user>>>u-2>>>traffic>>>downlink"},
            {"name":"inbound>>>api>>>traffic>>>uplink","value":"9"},
            {"name":"user>>>Not A User>>>traffic>>>uplink","value":"9"}
        ]}"#;
        assert_eq!(
            parse_statsquery(out).unwrap(),
            deltas(&[("u-1", 1200, 3400), ("u-2", 0, 0)])
        );
        assert!(parse_statsquery("{}").unwrap().is_empty());
        assert!(parse_statsquery("not json").is_err());
        assert!(
            parse_statsquery(r#"{"stat":[{"name":"user>>>u>>>traffic>>>uplink","value":"-1"}]}"#)
                .is_err()
        );
    }

    #[test]
    fn folds_saturate_and_reports_take_a_new_seq() {
        let mut store = TrafficStore::new().unwrap();
        store.fold(&deltas(&[("u-1", 10, 20)]));
        store.fold(&deltas(&[("u-1", 5, MAX_COUNT)]));
        let report = store.next_report("2026-10-29T10:00:00Z".into());
        assert_eq!(report.seq, 1);
        assert_eq!(report.users[0].uplink_bytes, 15);
        assert_eq!(report.users[0].downlink_bytes, MAX_COUNT);
        assert_eq!(store.next_report("2026-10-29T10:01:00Z".into()).seq, 2);
    }

    #[test]
    fn only_an_ack_of_the_latest_report_forgets_removed_users() {
        let mut store = TrafficStore::new().unwrap();
        store.fold(&deltas(&[("u-1", 1, 1), ("u-2", 2, 2)]));
        store.next_report("2026-10-29T10:00:00Z".into());
        store.next_report("2026-10-29T10:01:00Z".into());
        let managed: BTreeSet<String> = ["u-1".to_string()].into();
        let ack = |seq, epoch: &str| StatsAck {
            kind: "stats_ack".into(),
            counter_epoch: epoch.into(),
            acked_seq: seq,
        };
        assert!(!store.acknowledged(&ack(1, &store.counter_epoch.clone()), &managed));
        let other = TrafficStore::new().unwrap().counter_epoch;
        assert!(!store.acknowledged(&ack(2, &other), &managed));
        assert!(store.acknowledged(&ack(2, &store.counter_epoch.clone()), &managed));
        assert_eq!(store.users.keys().collect::<Vec<_>>(), ["u-1"]);
    }

    #[test]
    fn a_lost_or_malformed_store_starts_a_new_epoch() {
        let dir = std::env::temp_dir().join(format!("netagent-stats-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let first = TrafficStore::load(&dir).unwrap();
        first.save(&dir).unwrap();
        assert_eq!(TrafficStore::load(&dir).unwrap(), first);
        std::fs::write(dir.join(TRAFFIC_FILE), b"{").unwrap();
        assert_ne!(
            TrafficStore::load(&dir).unwrap().counter_epoch,
            first.counter_epoch
        );
        let _ = std::fs::remove_dir_all(&dir);
    }
}
