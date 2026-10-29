//! Meridian node agent (`netagent`): the walking skeleton of `meridian.agent/v1`.
//!
//! The agent dials the control service over HTTPS, long-polls for a signed
//! whole-node desired-state document, applies it, and reports observed state.
//! The contract lives in `src/meridian/core/agent_*.py` and
//! `contracts/meridian/v1/agent/`; this crate implements the skeleton subset
//! described in `agent/CLAUDE.md`.

pub mod agent;
pub mod apply;
pub mod bootstrap;
pub mod compose;
pub mod fetch;
pub mod fsutil;
pub mod jcs;
pub mod log;
pub mod model;
pub mod report;
pub mod state;
pub mod system;
pub mod timeutil;
pub mod verify;

/// The crate version, reported as `versions.agent`.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
