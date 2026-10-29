//! Concise stderr logging (journald adds timestamps and the unit name).
//!
//! Never pass a token, a user UUID, the service URL, or the content of a
//! sensitive file to these macros.

use std::sync::atomic::{AtomicBool, Ordering};

static DEBUG: AtomicBool = AtomicBool::new(false);

pub fn set_debug(on: bool) {
    DEBUG.store(on, Ordering::Relaxed);
}

pub fn debug_enabled() -> bool {
    DEBUG.load(Ordering::Relaxed)
}

#[macro_export]
macro_rules! info {
    ($($arg:tt)*) => { eprintln!($($arg)*) };
}

#[macro_export]
macro_rules! debug {
    ($($arg:tt)*) => {
        if $crate::log::debug_enabled() {
            eprintln!("debug: {}", format_args!($($arg)*));
        }
    };
}
