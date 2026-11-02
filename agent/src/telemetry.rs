//! Bounded local host readings, independent of per-person traffic collection.

use std::path::Path;

use crate::capacity_models::NodeCapacity;
use crate::fsutil::host_path;
use crate::identity::random_uuid;
use crate::model::Resource;
use crate::network_capacity::{MAX_SAFE_INTEGER, NetworkCollector};
use crate::report::{memory_field, parse_load_milli};
use crate::system::Exec;
use crate::timeutil::now_rfc3339;

pub(crate) struct CapacityCollector {
    epoch: String,
    sequence: u64,
    network: NetworkCollector,
    pub(crate) latest: Option<NodeCapacity>,
}

impl CapacityCollector {
    pub(crate) fn new() -> Result<Self, String> {
        Ok(Self {
            epoch: random_uuid()?,
            sequence: 0,
            network: NetworkCollector::default(),
            latest: None,
        })
    }

    pub(crate) fn collect(&mut self, root: &Path, exec: &dyn Exec, resources: &[Resource]) {
        if self.sequence == MAX_SAFE_INTEGER {
            let Ok(epoch) = random_uuid() else {
                self.latest = None;
                return;
            };
            self.epoch = epoch;
            self.sequence = 0;
        }
        self.sequence += 1;
        let read = |path| std::fs::read_to_string(host_path(root, path)).unwrap_or_default();
        let cpus = read("/proc/stat")
            .lines()
            .filter(|line| {
                line.split_whitespace().next().is_some_and(|name| {
                    name.strip_prefix("cpu").is_some_and(|number| {
                        !number.is_empty() && number.bytes().all(|b| b.is_ascii_digit())
                    })
                })
            })
            .count() as u64;
        let loads: Vec<_> = read("/proc/loadavg")
            .split_whitespace()
            .take(3)
            .map(parse_load_milli)
            .collect();
        let memory = read("/proc/meminfo");
        let total = memory_field(&memory, "MemTotal:").filter(|value| *value > 0);
        let available = memory_field(&memory, "MemAvailable:")
            .filter(|value| total.is_none_or(|total| *value <= total));
        self.latest = Some(NodeCapacity {
            collection_epoch: self.epoch.clone(),
            sequence: self.sequence,
            collected_at: now_rfc3339(),
            cpu_count: (1..=65_536).contains(&cpus).then_some(cpus),
            load1_milli: loads.first().copied().flatten(),
            load5_milli: loads.get(1).copied().flatten(),
            load15_milli: loads.get(2).copied().flatten(),
            memory_total_bytes: total,
            memory_available_bytes: available,
            network: self.network.collect(root, exec.now()),
            xray: crate::xray_health::process_capacity(root, exec, resources),
            // The pinned core exposes online maps only by client identity/address.
            inbound_connections: None,
        });
    }
}
