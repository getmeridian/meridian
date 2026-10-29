//! Xray API readiness tied to the managed core process.

use std::path::Path;
use std::time::{Duration, Instant};

use crate::fsutil::host_path;
use crate::model::{HealthCheck, Resource, Spec};
use crate::stats;
use crate::system::Exec;
use crate::timeutil::now_unix;

pub(crate) const CORE_UNIT: &str = "netcore.service";
const READY_TIMEOUT: Duration = Duration::from_secs(10);
const PROBE_TIMEOUT: Duration = Duration::from_secs(1);
const DEADLINE_ERROR: &str = "Xray API probe timed out";

/// The installed binary and validated loopback API from the held document.
pub(crate) fn api<'a>(root: &Path, resources: &'a [Resource]) -> Option<(String, &'a str)> {
    let binary = resources.iter().find_map(|r| match &r.spec {
        Spec::BinaryPin(pin) if pin.name == "xray" => Some(host_path(root, &pin.install_path)),
        _ => None,
    })?;
    let address = resources.iter().find_map(|r| match &r.spec {
        Spec::XrayConfig(config) => Some(config.api_address.as_str()),
        _ => None,
    })?;
    binary
        .is_file()
        .then(|| (binary.to_string_lossy().into_owned(), address))
}

fn remaining(exec: &dyn Exec, deadline: Instant) -> Result<Duration, &'static str> {
    let left = deadline.saturating_duration_since(exec.now());
    if left.is_zero() {
        Err(DEADLINE_ERROR)
    } else {
        Ok(left.min(PROBE_TIMEOUT))
    }
}

pub(crate) fn main_pid(exec: &dyn Exec, timeout: Duration) -> Result<u32, &'static str> {
    let output = exec
        .run_timeout(
            "systemctl",
            &["show", CORE_UNIT, "--property=ActiveState,MainPID"],
            &[],
            timeout,
        )
        .map_err(|_| "netcore process could not be checked")?;
    if !output.success {
        return Err("netcore process could not be checked");
    }
    let field = |key: &str| {
        output
            .stdout
            .lines()
            .find_map(|line| line.strip_prefix(key))
    };
    if !matches!(
        field("ActiveState="),
        Some("active" | "activating" | "reloading")
    ) {
        return Err("netcore is not running");
    }
    field("MainPID=")
        .and_then(|s| s.parse::<u32>().ok())
        .filter(|pid| *pid > 0)
        .ok_or("netcore has no main process")
}

fn same_process(exec: &dyn Exec, deadline: Instant, expected: u32) -> Result<(), &'static str> {
    if main_pid(exec, remaining(exec, deadline)?)? != expected {
        return Err("netcore process changed before API readiness");
    }
    Ok(())
}

/// A start is complete only when its original MainPID answers the stats API.
pub(crate) fn wait_ready(
    root: &Path,
    exec: &dyn Exec,
    resources: &[Resource],
) -> Result<(), &'static str> {
    let deadline = exec.now() + READY_TIMEOUT;
    let (binary, address) =
        api(root, resources).ok_or("Xray API is not configured or installed")?;
    let pid = main_pid(exec, remaining(exec, deadline)?)?;
    let mut pause = Duration::from_millis(100);
    loop {
        same_process(exec, deadline, pid)?;
        let result = stats::probe(exec, &binary, address, remaining(exec, deadline)?);
        same_process(exec, deadline, pid)?;
        if result.is_ok() {
            return Ok(());
        }
        let left = deadline.saturating_duration_since(exec.now());
        if left.is_zero() {
            return Err(DEADLINE_ERROR);
        }
        exec.sleep(pause.min(left));
        pause = (pause * 2).min(Duration::from_secs(1));
    }
}

pub(crate) fn wanted(resources: &[Resource]) -> bool {
    resources.iter().any(|r| matches!(&r.spec, Spec::Unit(unit) if unit.name == CORE_UNIT && unit.state == "started"))
}

/// One bounded, read-only sample for each health report, without a startup retry loop.
pub(crate) fn api_check(
    root: &Path,
    exec: &dyn Exec,
    resources: &[Resource],
) -> Option<HealthCheck> {
    if !wanted(resources) {
        return None;
    }
    let result = (|| {
        let deadline = exec.now() + PROBE_TIMEOUT;
        let (binary, address) =
            api(root, resources).ok_or("Xray API is not configured or installed")?;
        let pid = main_pid(exec, remaining(exec, deadline)?)?;
        let queried = stats::probe(exec, &binary, address, remaining(exec, deadline)?);
        same_process(exec, deadline, pid)?;
        queried
    })();
    Some(HealthCheck {
        name: "xray.api".into(),
        status: if result.is_ok() { "pass" } else { "fail" },
        message: result.err().unwrap_or("Xray API is ready").into(),
    })
}

/// /proc starttime is field 22 in clock ticks after boot; comm may contain spaces and `)`.
fn process_uptime(
    stat: &str,
    boot_stat: &str,
    pid: u32,
    ticks_per_second: u64,
    now: u64,
) -> Option<u64> {
    let (identity, fields) = stat.rsplit_once(')')?;
    let (reported_pid, _) = identity.split_once('(')?;
    if reported_pid.trim().parse::<u32>().ok()? != pid {
        return None;
    }
    let mut fields = fields.split_whitespace();
    if !matches!(
        fields.next()?,
        "R" | "S" | "D" | "T" | "t" | "W" | "I" | "P"
    ) {
        return None;
    }
    // State was field 3; skip fields 4 through 21 to reach starttime.
    let started_ticks = fields.nth(18)?.parse::<u64>().ok()?;
    let boot = boot_stat
        .lines()
        .find_map(|line| line.strip_prefix("btime ")?.trim().parse::<u64>().ok())?;
    let started = boot.checked_add(started_ticks.checked_div(ticks_per_second)?)?;
    now.checked_sub(started)
}

pub(crate) fn uptime_check(
    root: &Path,
    exec: &dyn Exec,
    resources: &[Resource],
) -> Option<HealthCheck> {
    if !wanted(resources) {
        return None;
    }
    let uptime = (|| {
        let deadline = exec.now() + PROBE_TIMEOUT;
        let pid = main_pid(exec, remaining(exec, deadline).ok()?).ok()?;
        let stat = std::fs::read_to_string(host_path(root, &format!("/proc/{pid}/stat"))).ok()?;
        let boot_stat = std::fs::read_to_string(host_path(root, "/proc/stat")).ok()?;
        // SAFETY: sysconf has no pointer arguments; a nonpositive tick rate is unavailable.
        let ticks = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };
        let ticks = u64::try_from(ticks).ok().filter(|ticks| *ticks > 0)?;
        let uptime = process_uptime(&stat, &boot_stat, pid, ticks, now_unix())?;
        same_process(exec, deadline, pid).ok()?;
        Some(uptime)
    })();
    Some(HealthCheck {
        name: "xray.uptime".into(),
        status: if uptime.is_some() { "pass" } else { "warn" },
        message: uptime.map_or_else(
            || "core uptime unavailable".into(),
            |seconds| format!("up {seconds}s"),
        ),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stat(pid: u32, state: &str, ticks: &str) -> String {
        format!(
            "{pid} (netcore ) worker) {state} {} {ticks} 0 0",
            vec!["0"; 18].join(" ")
        )
    }

    #[test]
    fn proc_starttime_uses_boot_time_and_the_hosts_tick_rate() {
        let process = stat(42, "S", "4997500");
        assert_eq!(
            process_uptime(&process, "cpu 1 2 3\nbtime 1000\n", 42, 250, 21000),
            Some(10)
        );
        assert_eq!(
            process_uptime(&stat(42, "R", "4999750"), "btime 1000\n", 42, 250, 21000),
            Some(1)
        );
    }

    #[test]
    fn missing_dead_mismatched_or_future_process_evidence_is_unavailable() {
        let valid = stat(42, "S", "100");
        for (process, boot, pid, ticks, now) in [
            ("broken".to_string(), "btime 1000", 42, 100, 1010),
            (stat(42, "S", "invalid"), "btime 1000", 42, 100, 1010),
            (stat(42, "Z", "100"), "btime 1000", 42, 100, 1010),
            (valid.clone(), "btime 1000", 43, 100, 1010),
            (valid.clone(), "cpu 0 0 0", 42, 100, 1010),
            (valid.clone(), "btime 1000", 42, 0, 1010),
            (valid.clone(), "btime 2000", 42, 100, 1010),
            (valid, "btime 18446744073709551615", 42, 100, 1010),
        ] {
            assert_eq!(process_uptime(&process, boot, pid, ticks, now), None);
        }
    }
}
