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
pub mod bootstrap;
pub mod compose;
pub mod fetch;
pub mod fsutil;
pub mod identity;
pub mod jcs;
pub mod log;
pub mod model;
pub mod report;
pub mod session;
pub mod state;
pub mod stats;
pub mod system;
pub mod timeutil;
pub mod verify;
pub mod xray;

/// The crate version, reported as `versions.agent`.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
