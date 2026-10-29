//! Path allowlist, host-root mapping, and atomic file writes.

use std::fs::{self, File, OpenOptions};
use std::io::{self, ErrorKind, Read, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

/// The only directories the agent writes desired files into.
pub const ALLOWED_PREFIXES: [&str; 2] = ["/etc/netagent/", "/usr/local/lib/netagent/"];
pub const UNIT_DIR: &str = "/etc/systemd/system";
/// The agent's own unit, which desired state must never manage.
pub const AGENT_UNIT: &str = "netagent.service";

/// Whether a desired absolute path is inside the allowlist and free of traversal.
pub fn check_path(path: &str) -> Result<(), String> {
    let inside = ALLOWED_PREFIXES
        .iter()
        .any(|prefix| path.len() > prefix.len() && path.starts_with(prefix));
    let clean = path
        .split('/')
        .skip(1)
        .all(|segment| !segment.is_empty() && !segment.starts_with('.') && !segment.contains('\0'));
    if inside && clean {
        Ok(())
    } else {
        Err(format!(
            "{path} is outside {}",
            ALLOWED_PREFIXES.join(" and ")
        ))
    }
}

/// Whether a unit name is one the agent may manage: `net[a-z0-9-]*.service`, never its own.
pub fn check_unit_name(name: &str) -> Result<(), String> {
    let stem = name
        .strip_prefix("net")
        .and_then(|rest| rest.strip_suffix(".service"));
    let valid = stem.is_some_and(|stem| {
        stem.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
    });
    if valid && name != AGENT_UNIT {
        Ok(())
    } else {
        Err(format!(
            "unit {name} is not an agent-managed net*.service unit"
        ))
    }
}

/// Map a desired absolute path onto the host root (`/` in production, a temp dir in tests).
pub fn host_path(root: &Path, absolute: &str) -> PathBuf {
    root.join(absolute.trim_start_matches('/'))
}

/// SHA-256 of a file, `None` when it does not exist.
pub fn sha256_file(path: &Path) -> io::Result<Option<String>> {
    let mut file = match File::open(path) {
        Ok(file) => file,
        Err(err) if err.kind() == ErrorKind::NotFound => return Ok(None),
        Err(err) => return Err(err),
    };
    let mut hasher = Sha256::new();
    let mut buf = vec![0u8; 64 * 1024];
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(Some(crate::jcs::hex(&hasher.finalize())))
}

/// Read a file, `None` when it does not exist.
pub fn read_optional(path: &Path) -> io::Result<Option<Vec<u8>>> {
    match fs::read(path) {
        Ok(bytes) => Ok(Some(bytes)),
        Err(err) if err.kind() == ErrorKind::NotFound => Ok(None),
        Err(err) => Err(err),
    }
}

/// A temporary sibling of `dest` (same directory, so the final rename is atomic).
pub fn temp_sibling(dest: &Path, suffix: &str) -> PathBuf {
    let name = dest
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    dest.with_file_name(format!(".{name}.{suffix}"))
}

/// Create a new private (0600) temporary file next to `dest`, creating parent directories.
pub fn create_temp(dest: &Path, suffix: &str) -> io::Result<(PathBuf, File)> {
    if let Some(parent) = dest.parent() {
        fs::create_dir_all(parent)?;
    }
    let tmp = temp_sibling(dest, suffix);
    let _ = fs::remove_file(&tmp);
    let file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&tmp)?;
    Ok((tmp, file))
}

/// Give a synced temporary file its final mode and owner, then rename it over `dest`.
pub fn commit(tmp: &Path, dest: &Path, mode: u32, owner: Option<(u32, u32)>) -> io::Result<()> {
    let result = (|| {
        fs::set_permissions(tmp, fs::Permissions::from_mode(mode))?;
        if let Some((uid, gid)) = owner {
            let meta = fs::metadata(tmp)?;
            if meta.uid() != uid || meta.gid() != gid {
                std::os::unix::fs::chown(tmp, Some(uid), Some(gid))?;
            }
        }
        fs::rename(tmp, dest)?;
        if let Some(parent) = dest.parent() {
            File::open(parent)?.sync_all()?;
        }
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(tmp);
    }
    result
}

/// Write `data` atomically: private temp file, fsync, mode/owner, rename, fsync the directory.
pub fn write_atomic(
    dest: &Path,
    data: &[u8],
    mode: u32,
    owner: Option<(u32, u32)>,
) -> io::Result<()> {
    let (tmp, mut file) = create_temp(dest, "netagent-tmp")?;
    let written = file.write_all(data).and_then(|()| file.sync_all());
    drop(file);
    if let Err(err) = written {
        let _ = fs::remove_file(&tmp);
        return Err(err);
    }
    commit(&tmp, dest, mode, owner)
}

/// Ensure an existing file has the mode and owner asked for; true when something changed.
pub fn ensure_mode_owner(path: &Path, mode: u32, owner: Option<(u32, u32)>) -> io::Result<bool> {
    let meta = fs::metadata(path)?;
    let mut changed = false;
    if meta.permissions().mode() & 0o7777 != mode {
        fs::set_permissions(path, fs::Permissions::from_mode(mode))?;
        changed = true;
    }
    if let Some((uid, gid)) = owner
        && (meta.uid() != uid || meta.gid() != gid)
    {
        std::os::unix::fs::chown(path, Some(uid), Some(gid))?;
        changed = true;
    }
    Ok(changed)
}

/// Remove a file; an absent file is already removed.
pub fn remove_if_present(path: &Path) -> io::Result<()> {
    match fs::remove_file(path) {
        Err(err) if err.kind() != ErrorKind::NotFound => Err(err),
        _ => Ok(()),
    }
}

/// Look up a numeric ID by name in `<root>/etc/passwd` or `<root>/etc/group`.
pub fn lookup_id(root: &Path, database: &str, name: &str) -> Option<u32> {
    let text = fs::read_to_string(host_path(root, &format!("/etc/{database}"))).ok()?;
    text.lines().find_map(|line| {
        let mut fields = line.split(':');
        (fields.next()? == name).then_some(())?;
        fields.nth(1)?.parse().ok()
    })
}

/// The protocol error code of a failed write.
pub fn write_error_code(err: &io::Error) -> &'static str {
    if err.raw_os_error() == Some(libc::ENOSPC) {
        "disk_full"
    } else {
        "write_failed"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn paths_must_stay_inside_the_allowlist() {
        assert!(check_path("/etc/netagent/core.json").is_ok());
        assert!(check_path("/usr/local/lib/netagent/netcore").is_ok());
        for bad in [
            "/etc/netagent/",
            "/etc/netagent",
            "/etc/passwd",
            "/etc/netagent/../shadow",
            "/etc/netagent/.hidden",
            "/etc/netagent//x",
            "/usr/local/lib/netagentx/y",
            "etc/netagent/core.json",
        ] {
            assert!(check_path(bad).is_err(), "{bad} should be refused");
        }
    }

    #[test]
    fn units_must_be_net_services_other_than_the_agent() {
        assert!(check_unit_name("netcore.service").is_ok());
        assert!(check_unit_name("netfwd-a1.service").is_ok());
        for bad in [
            "netagent.service",
            "sshd.service",
            "netcore.timer",
            "net_core.service",
            "netCore.service",
            "../netcore.service",
        ] {
            assert!(check_unit_name(bad).is_err(), "{bad} should be refused");
        }
    }

    #[test]
    fn passwd_lookup_reads_the_third_field() {
        let root = std::env::temp_dir().join(format!("netagent-fsutil-{}", std::process::id()));
        fs::create_dir_all(root.join("etc")).unwrap();
        fs::write(
            root.join("etc/passwd"),
            "root:x:0:0::/root:/bin/sh\nxray:x:995:994::/:/bin/false\n",
        )
        .unwrap();
        assert_eq!(lookup_id(&root, "passwd", "xray"), Some(995));
        assert_eq!(lookup_id(&root, "passwd", "nobody"), None);
        fs::remove_dir_all(root).unwrap();
    }
}
