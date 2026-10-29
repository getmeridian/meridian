//! Applying managed systemd units and restoring the previous artifacts after a failed start.

use std::collections::BTreeSet;
use std::path::PathBuf;

use crate::apply::{Engine, Failure, Outcome, last_line, truncate};
use crate::fsutil::{self, host_path};
use crate::info;
use crate::model::{Resource, UnitSpec};
use crate::state::LedgerEntry;
use crate::xray_health;

impl Engine<'_> {
    // ---- systemd_unit ---------------------------------------------------------

    pub(crate) fn unit_path(&self, name: &str) -> PathBuf {
        host_path(&self.root, &format!("{}/{name}", fsutil::UNIT_DIR))
    }

    pub(crate) fn systemctl(&self, args: &[&str]) -> Result<String, String> {
        match self.exec.run("systemctl", args, &[]) {
            Ok(output) if output.success => Ok(output.stdout),
            Ok(output) => Err(format!(
                "systemctl {} failed: {}",
                args.join(" "),
                truncate(last_line(&output.stdout, &output.stderr), 200)
            )),
            Err(err) => Err(format!("cannot run systemctl: {err}")),
        }
    }

    pub(crate) fn query(&self, verb: &str, name: &str) -> String {
        self.exec
            .run("systemctl", &[verb, name], &[])
            .map(|output| output.stdout.trim().to_string())
            .unwrap_or_default()
    }

    fn journal_tail(&self, name: &str) -> String {
        let lines = self
            .exec
            .run(
                "journalctl",
                &["-u", name, "-n", "5", "--no-pager", "-o", "cat"],
                &[],
            )
            .map(|output| output.stdout)
            .unwrap_or_default();
        let joined = lines
            .lines()
            .map(str::trim)
            .filter(|l| !l.is_empty())
            .collect::<Vec<_>>()
            .join(" | ");
        truncate(&joined, 300)
    }

    fn start_and_check(
        &self,
        spec: &UnitSpec,
        resources: &[Resource],
        verb: &str,
    ) -> Result<(), String> {
        let ran = self.systemctl(&[verb, &spec.name]);
        if spec.name == xray_health::CORE_UNIT {
            ran.map_err(|_| format!("netcore could not {verb}"))?;
            return xray_health::wait_ready(&self.root, self.exec, resources)
                .map_err(|reason| format!("{reason} after {verb}"));
        }
        self.exec.sleep(self.settle);
        let active = self.query("is-active", &spec.name) == "active";
        if ran.is_ok() && active {
            Ok(())
        } else {
            Err(format!(
                "{} is not active after {verb}: {}",
                spec.name,
                self.journal_tail(&spec.name)
            ))
        }
    }

    pub(crate) fn apply_unit(
        &self,
        spec: &UnitSpec,
        resources: &[Resource],
        changed: &BTreeSet<String>,
        previous: Option<&LedgerEntry>,
    ) -> Outcome {
        // An entry without an installed hash is a unit that did not finish last time.
        let owed = previous.is_some_and(|entry| entry.installed_sha256.is_none());
        match self.try_unit(spec, resources, changed, owed) {
            Ok(outcome) => outcome,
            Err(failure) => Outcome::Failed(failure),
        }
    }

    fn try_unit(
        &self,
        spec: &UnitSpec,
        resources: &[Resource],
        changed: &BTreeSet<String>,
        owed: bool,
    ) -> Result<Outcome, Failure> {
        fsutil::check_unit_name(&spec.name).map_err(|msg| Failure::new("path_not_allowed", msg))?;
        let path = self.unit_path(&spec.name);
        let service_failed = |msg: String| Failure::new("service_failed", msg);
        let current =
            fsutil::read_optional(&path).map_err(|err| Failure::io(&err, "cannot read unit"))?;
        let unit_changed = current.as_deref() != Some(spec.content.as_bytes());
        if unit_changed {
            fsutil::write_atomic(&path, spec.content.as_bytes(), 0o644, None)
                .map_err(|err| Failure::io(&err, "cannot write unit"))?;
            self.systemctl(&["daemon-reload"]).map_err(service_failed)?;
        }
        let enabled = self.query("is-enabled", &spec.name) == "enabled";
        if enabled != spec.enabled {
            let verb = if spec.enabled { "enable" } else { "disable" };
            self.systemctl(&[verb, &spec.name])
                .map_err(service_failed)?;
        }
        let active = self.query("is-active", &spec.name) == "active";
        let restart = owed || unit_changed || spec.restart_on.iter().any(|id| changed.contains(id));
        let mut acted = false;
        if spec.state == "started" {
            let verb = match (active, restart) {
                (false, _) => Some("start"),
                (true, true) => Some("restart"),
                (true, false) => None,
            };
            if let Some(verb) = verb {
                acted = true;
                if let Err(mut message) = self.start_and_check(spec, resources, verb) {
                    // Passing `run -test` is not starting (a port may be taken): bring back
                    // what ran before rather than leave the node without its core.
                    let restored = self.restore_kept(spec, resources);
                    if !restored.is_empty() {
                        let back = self.start_and_check(spec, resources, "restart").is_ok();
                        let outcome = if back {
                            "and it runs again"
                        } else {
                            "but it still does not run"
                        };
                        info!(
                            "{} failed on the new {}; restored the previous version {outcome}",
                            spec.name,
                            restored.join(", ")
                        );
                        message.push_str(&format!(
                            "; restored the previous {} {outcome}",
                            restored.join(", ")
                        ));
                    }
                    return Err(service_failed(message));
                }
                info!("{verb}ed {}", spec.name);
            }
        } else if active {
            acted = true;
            self.systemctl(&["stop", &spec.name])
                .map_err(service_failed)?;
            info!("stopped {}", spec.name);
        }
        Ok(Outcome::Applied {
            changed: unit_changed || acted,
            installed: Some(crate::jcs::sha256_hex(spec.content.as_bytes())),
        })
    }
}
