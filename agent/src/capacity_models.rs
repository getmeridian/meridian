//! Nullable, integer-only capacity contract; no host or client addresses.

use serde::Serialize;

#[derive(Serialize, Clone, Debug)]
pub struct NetworkCapacity {
    pub counter_epoch: String,
    pub rx_bytes: u64,
    pub tx_bytes: u64,
    pub rx_bytes_per_second: Option<u64>,
    pub tx_bytes_per_second: Option<u64>,
    pub interval_millis: Option<u64>,
}

#[derive(Serialize, Clone, Debug)]
pub struct ProcessCapacity {
    pub uptime_seconds: Option<u64>,
    pub memory_resident_bytes: Option<u64>,
}

#[derive(Serialize, Clone, Debug)]
pub struct InboundConnections {
    pub inbound_tag: String,
    pub connections: u64,
}

#[derive(Serialize, Clone, Debug)]
pub struct NodeCapacity {
    pub collection_epoch: String,
    pub sequence: u64,
    pub collected_at: String,
    pub cpu_count: Option<u64>,
    pub load1_milli: Option<u64>,
    pub load5_milli: Option<u64>,
    pub load15_milli: Option<u64>,
    pub memory_total_bytes: Option<u64>,
    pub memory_available_bytes: Option<u64>,
    pub network: Option<NetworkCapacity>,
    pub xray: Option<ProcessCapacity>,
    pub inbound_connections: Option<Vec<InboundConnections>>,
}
