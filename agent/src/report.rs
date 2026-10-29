//! Health and versions for `ObservedStateReport`.

use std::ffi::CString;
use std::path::Path;

use crate::fsutil::host_path;
use crate::model::{HealthCheck, NodeHealth, NodeVersions, Resource, ResourceObservation, Spec};
use crate::system::Exec;

const MAX_CHECKS: usize = 32;

/// Node health: one check per systemd unit resource, plus host figures from /proc.
pub fn health(
    root: &Path,
    state_dir: &Path,
    exec: &dyn Exec,
    resources: &[Resource],
    observations: &[ResourceObservation],
) -> NodeHealth {
    let mut checks: Vec<_> = crate::xray_health::api_check(root, exec, resources)
        .into_iter()
        .collect();
    for resource in resources {
        let Spec::Unit(unit) = &resource.spec else {
            continue;
        };
        if checks.len() == MAX_CHECKS {
            break;
        }
        let state = exec
            .run("systemctl", &["is-active", &unit.name], &[])
            .map(|out| out.stdout.trim().to_string())
            .unwrap_or_default();
        let state = if state.is_empty() {
            "unknown".to_string()
        } else {
            state
        };
        let want_active = unit.state == "started";
        let status = match (want_active, state == "active") {
            (true, true) | (false, false) => "pass",
            (true, false) => "fail",
            (false, true) => "warn",
        };
        checks.push(HealthCheck {
            name: check_name(&resource.id),
            status,
            message: format!("{} is {}", unit.name, sanitize_message(&state)),
        });
    }
    let resource_trouble = observations.iter().any(|obs| obs.status != "applied");
    let status = if checks.iter().any(|c| c.status == "fail") {
        "unhealthy"
    } else if resource_trouble || checks.iter().any(|c| c.status == "warn") {
        "degraded"
    } else {
        "healthy"
    };
    let proc_text = |name: &str| std::fs::read_to_string(host_path(root, name)).unwrap_or_default();
    NodeHealth {
        status,
        checks,
        uptime_seconds: leading_integer(&proc_text("/proc/uptime")),
        load1_milli: load_milli(&proc_text("/proc/loadavg")),
        memory_available_bytes: mem_available(&proc_text("/proc/meminfo")),
        disk_free_bytes: disk_free(state_dir),
    }
}

/// Health check names allow `[a-z][a-z0-9_.-]*` (64); resource IDs may hold `:` or start with a digit.
pub fn check_name(resource_id: &str) -> String {
    let mut name: String = resource_id
        .chars()
        .map(|c| {
            if c.is_ascii_lowercase() || c.is_ascii_digit() || "_.-".contains(c) {
                c
            } else {
                '-'
            }
        })
        .collect();
    if !name.starts_with(|c: char| c.is_ascii_lowercase()) {
        name.insert(0, 'u');
    }
    name.truncate(64);
    name
}

fn sanitize_message(text: &str) -> String {
    text.chars()
        .filter(|c| c.is_ascii_alphanumeric() || *c == '-')
        .take(32)
        .collect()
}

fn leading_integer(text: &str) -> u64 {
    text.split(|c: char| !c.is_ascii_digit())
        .next()
        .and_then(|s| s.parse().ok())
        .unwrap_or(0)
}

/// "0.52 0.40 ..." → 520, without floats.
pub fn load_milli(text: &str) -> u64 {
    let first = text.split_whitespace().next().unwrap_or("");
    let (whole, frac) = first.split_once('.').unwrap_or((first, ""));
    let whole: u64 = whole.parse().unwrap_or(0);
    let mut digits: String = frac.chars().take(3).collect();
    while digits.len() < 3 {
        digits.push('0');
    }
    whole * 1000 + digits.parse::<u64>().unwrap_or(0)
}

fn mem_available(text: &str) -> u64 {
    text.lines()
        .find_map(|line| line.strip_prefix("MemAvailable:"))
        .and_then(|rest| rest.split_whitespace().next())
        .and_then(|kb| kb.parse::<u64>().ok())
        .map_or(0, |kb| kb * 1024)
}

#[allow(clippy::unnecessary_cast, clippy::useless_conversion)]
fn disk_free(path: &Path) -> u64 {
    let Ok(c_path) = CString::new(path.as_os_str().as_encoded_bytes()) else {
        return 0;
    };
    // SAFETY: statvfs writes into a zeroed struct we own; the path is NUL-terminated.
    unsafe {
        let mut stat: libc::statvfs = std::mem::zeroed();
        if libc::statvfs(c_path.as_ptr(), &mut stat) != 0 {
            return 0;
        }
        (stat.f_bavail as u64).saturating_mul(stat.f_frsize as u64)
    }
}

/// Versions: the agent's own, the applied xray pin's, and the host's OS and kernel.
pub fn versions(
    root: &Path,
    resources: &[Resource],
    observations: &[ResourceObservation],
) -> NodeVersions {
    let xray = resources.iter().find_map(|r| match &r.spec {
        Spec::BinaryPin(pin)
            if pin.name == "xray"
                && observations
                    .iter()
                    .any(|o| o.id == r.id && o.status == "applied") =>
        {
            Some(pin.version.clone())
        }
        _ => None,
    });
    let os_release =
        std::fs::read_to_string(host_path(root, "/etc/os-release")).unwrap_or_default();
    let field = |key: &str| {
        os_release
            .lines()
            .find_map(|line| line.strip_prefix(key)?.strip_prefix('='))
            .map(|v| v.trim().trim_matches('"').to_string())
            .unwrap_or_default()
    };
    let os_id = clean(
        &field("ID"),
        |c| c.is_ascii_lowercase() || c.is_ascii_digit() || "._-".contains(c),
        32,
    );
    let os_version = clean(
        &field("VERSION_ID"),
        |c| c.is_ascii_alphanumeric() || "._-".contains(c),
        32,
    );
    let kernel = clean(
        &kernel_release(),
        |c| c.is_ascii_alphanumeric() || "._+~-".contains(c),
        64,
    );
    NodeVersions {
        agent: crate::VERSION.to_string(),
        xray,
        realm: None,
        nginx: None,
        os_id: if os_id.is_empty() {
            "unknown".into()
        } else {
            os_id
        },
        os_version,
        kernel: if kernel.is_empty() {
            "unknown".into()
        } else {
            kernel
        },
    }
}

fn clean(text: &str, allowed: impl Fn(char) -> bool, max: usize) -> String {
    text.chars().filter(|c| allowed(*c)).take(max).collect()
}

fn kernel_release() -> String {
    // SAFETY: uname fills a zeroed struct we own; `release` is NUL-terminated.
    unsafe {
        let mut name: libc::utsname = std::mem::zeroed();
        if libc::uname(&mut name) != 0 {
            return String::new();
        }
        std::ffi::CStr::from_ptr(name.release.as_ptr())
            .to_string_lossy()
            .into_owned()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn load_is_parsed_without_floats() {
        assert_eq!(load_milli("0.52 0.40 0.30 1/100 42\n"), 520);
        assert_eq!(load_milli("12.5 1 1"), 12_500);
        assert_eq!(load_milli(""), 0);
    }

    #[test]
    fn check_names_follow_the_contract_pattern() {
        assert_eq!(check_name("unit:netcore"), "unit-netcore");
        assert_eq!(check_name("9core"), "u9core");
    }
}
