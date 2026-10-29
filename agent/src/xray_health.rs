//! Xray API readiness tied to the managed core process.

use std::path::Path;
use std::time::{Duration, Instant};

use crate::fsutil::host_path;
use crate::model::{HealthCheck, Resource, Spec};
use crate::stats;
use crate::system::Exec;

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
