//! One authoritative run process per state directory; staged checks never take this lock.

use std::fs::{File, OpenOptions};
use std::io;
use std::os::fd::AsRawFd;
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;

pub const LOCK_FILE: &str = "run.lock";

/// Closing the descriptor releases the lock, including when systemd stops the old process.
/// Never unlink the file: a new inode would allow another process to acquire a second lock.
pub struct InstanceLock {
    _file: File,
}

impl InstanceLock {
    pub fn acquire(state_dir: &Path) -> Result<Self, String> {
        std::fs::create_dir_all(state_dir)
            .map_err(|err| format!("cannot create state directory: {err}"))?;
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW)
            .open(state_dir.join(LOCK_FILE))
            .map_err(|err| format!("cannot open state directory lock: {err}"))?;
        // SAFETY: the file owns a valid descriptor; flock does not outlive or take ownership of it.
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            let err = io::Error::last_os_error();
            return Err(if err.kind() == io::ErrorKind::WouldBlock {
                "another netagent instance is already running in this state directory".into()
            } else {
                format!("cannot lock state directory: {err}")
            });
        }
        Ok(Self { _file: file })
    }
}
