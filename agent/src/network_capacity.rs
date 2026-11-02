//! Local default-route interface counters. Interface identity never leaves the node.

use std::path::Path;
use std::time::Instant;

use crate::capacity_models::NetworkCapacity;
use crate::fsutil::host_path;
use crate::identity::random_uuid;

pub(crate) const MAX_SAFE_INTEGER: u64 = (1 << 53) - 1;

struct Baseline {
    identity: String,
    raw_rx: u64,
    raw_tx: u64,
    at: Instant,
    counter: NetworkCapacity,
}

#[derive(Default)]
pub(crate) struct NetworkCollector {
    baseline: Option<Baseline>,
}

fn interface_name(name: &str) -> bool {
    !name.is_empty()
        && name != "lo"
        && name.len() <= 15
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_-.:".contains(&b))
        && name != "."
        && name != ".."
}

/// IPv4's least-metric active default route; IPv6 is the fallback for IPv6-only hosts.
fn default_interface(root: &Path) -> Option<String> {
    let ipv4 = std::fs::read_to_string(host_path(root, "/proc/net/route")).unwrap_or_default();
    let mut routes: Vec<(u64, String)> = ipv4
        .lines()
        .filter_map(|line| {
            let fields: Vec<_> = line.split_whitespace().collect();
            if fields.len() < 8
                || !interface_name(fields[0])
                || fields[1] != "00000000"
                || fields[7] != "00000000"
                || u64::from_str_radix(fields[3], 16).ok()? & 1 == 0
            {
                return None;
            }
            Some((fields[6].parse().ok()?, fields[0].to_string()))
        })
        .collect();
    if routes.is_empty() {
        let ipv6 =
            std::fs::read_to_string(host_path(root, "/proc/net/ipv6_route")).unwrap_or_default();
        routes = ipv6
            .lines()
            .filter_map(|line| {
                let fields: Vec<_> = line.split_whitespace().collect();
                if fields.len() != 10
                    || fields[0] != "00000000000000000000000000000000"
                    || fields[1] != "00"
                    || fields[3] != "00"
                    || !interface_name(fields[9])
                    || u64::from_str_radix(fields[8], 16).ok()? & 1 == 0
                {
                    return None;
                }
                Some((
                    u64::from_str_radix(fields[5], 16).ok()?,
                    fields[9].to_string(),
                ))
            })
            .collect();
    }
    routes.sort();
    routes.into_iter().next().map(|(_, name)| name)
}

impl NetworkCollector {
    pub(crate) fn collect(&mut self, root: &Path, now: Instant) -> Option<NetworkCapacity> {
        let read = (|| {
            let name = default_interface(root)?;
            let read = |suffix: &str| {
                std::fs::read_to_string(host_path(root, &format!("/sys/class/net/{name}/{suffix}")))
                    .ok()?
                    .trim()
                    .parse::<u64>()
                    .ok()
            };
            let index = read("ifindex").filter(|index| *index > 0)?;
            let rx = read("statistics/rx_bytes")?;
            let tx = read("statistics/tx_bytes")?;
            Some((format!("{name}:{index}"), rx, tx))
        })();
        let Some((identity, rx, tx)) = read else {
            self.baseline = None;
            return None;
        };
        self.sample(identity, rx, tx, now).ok()
    }

    fn sample(
        &mut self,
        identity: String,
        raw_rx: u64,
        raw_tx: u64,
        now: Instant,
    ) -> Result<NetworkCapacity, String> {
        let growth = self.baseline.as_ref().and_then(|previous| {
            if previous.identity != identity || now < previous.at {
                return None;
            }
            let rx = raw_rx.checked_sub(previous.raw_rx)?;
            let tx = raw_tx.checked_sub(previous.raw_tx)?;
            let rx_total = previous.counter.rx_bytes.checked_add(rx)?;
            let tx_total = previous.counter.tx_bytes.checked_add(tx)?;
            (rx_total <= MAX_SAFE_INTEGER && tx_total <= MAX_SAFE_INTEGER).then_some((
                rx,
                tx,
                rx_total,
                tx_total,
                now.duration_since(previous.at),
            ))
        });
        let counter = if let Some((rx, tx, rx_total, tx_total, interval)) = growth {
            let millis = u64::try_from(interval.as_millis())
                .ok()
                .filter(|millis| (1..=180_000).contains(millis));
            let rate = |bytes: u64| {
                millis.and_then(|millis| {
                    u64::try_from(u128::from(bytes) * 1000 / u128::from(millis))
                        .ok()
                        .filter(|rate| *rate <= MAX_SAFE_INTEGER)
                })
            };
            let rx_rate = rate(rx);
            let tx_rate = rate(tx);
            let interval_millis = millis.filter(|_| rx_rate.is_some() && tx_rate.is_some());
            NetworkCapacity {
                counter_epoch: self
                    .baseline
                    .as_ref()
                    .unwrap()
                    .counter
                    .counter_epoch
                    .clone(),
                rx_bytes: rx_total,
                tx_bytes: tx_total,
                rx_bytes_per_second: interval_millis.and(rx_rate),
                tx_bytes_per_second: interval_millis.and(tx_rate),
                interval_millis,
            }
        } else {
            NetworkCapacity {
                counter_epoch: random_uuid()?,
                rx_bytes: 0,
                tx_bytes: 0,
                rx_bytes_per_second: None,
                tx_bytes_per_second: None,
                interval_millis: None,
            }
        };
        self.baseline = Some(Baseline {
            identity,
            raw_rx,
            raw_tx,
            at: now,
            counter: counter.clone(),
        });
        Ok(counter)
    }
}
