//! Meridian node agent (`netagent`) for `meridian.agent/v1`.
//!
//! The agent enrolls under the fleet CA, opens sessions with signed hellos,
//! long-polls the control service over HTTPS for a signed whole-node
//! desired-state document, applies it, and reports observed state and traffic.
//! The contract lives in `src/meridian/core/agent_*.py` and
//! `contracts/meridian/v1/agent/`; `agent/CLAUDE.md` lists what this crate
//! implements of it.

pub mod agent;
pub mod apply;
mod backoff;
pub mod bootstrap;
mod command_core;
mod command_host;
pub mod commands;
pub mod compose;
pub mod fetch;
pub mod fsutil;
pub mod identity;
pub mod instance;
pub mod jcs;
pub mod log;
pub mod model;
pub mod report;
pub mod selfcheck;
pub mod session;
pub mod state;
pub mod stats;
pub mod system;
pub mod timeutil;
mod unit;
pub mod verify;
pub mod xray;
mod xray_flow;
mod xray_health;
mod xray_hysteria;
pub mod xray_identity;
mod xray_revocations;

/// The build version (`build.rs`), reported as `agent_version` and `versions.agent`.
pub const VERSION: &str = env!("NETAGENT_VERSION");
