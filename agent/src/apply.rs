//! The apply engine: converge one verified document, front to back, then report per resource.
//!
//! Every pass also re-checks resources already applied (file hashes, composed
//! config bytes, unit content and run state) and repairs drift. Resources the
//! ledger holds but the document no longer names are retired first, dependents
//! first, unless the new document claims the same path or unit.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::fs::{self, File};
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::time::Duration;

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD;
use serde_json::Value;

use crate::fetch::{Fetch, HashingWriter};
use crate::fsutil::{self, host_path};
use crate::model::{
    BinaryPinSpec, FileSpec, Resource, ResourceError, ResourceObservation, Spec, UnitSpec,
    parse_mode,
};
use crate::state::LedgerEntry;
use crate::system::Exec;
use crate::xray::users_outcome;
use crate::{debug, info};

/// Largest file an archive member may extract to.
const MAX_EXTRACTED_BYTES: u64 = 512 * 1024 * 1024;

pub struct Engine<'a> {
    /// Host root every desired absolute path is joined onto (`/` in production).
    pub root: PathBuf,
    /// Download cache for one pass, removed afterwards.
    pub cache_dir: PathBuf,
    pub exec: &'a dyn Exec,
    pub fetch: &'a dyn Fetch,
    /// Pause after a start or restart before checking the unit is active.
    pub settle: Duration,
    /// Accept http:// sources (tests only).
    pub allow_http: bool,
}

#[derive(Debug)]
pub(crate) struct Failure {
    pub code: &'static str,
    pub message: String,
    pub retryable: bool,
}

impl Failure {
    pub(crate) fn new(code: &'static str, message: impl Into<String>) -> Failure {
        let message: String = message.into();
        let retryable = !matches!(
            code,
            "unsupported" | "path_not_allowed" | "validation_failed"
        );
        Failure {
            code,
            message: truncate(&message, 900),
            retryable,
        }
    }

    pub(crate) fn io(err: &io::Error, what: &str) -> Failure {
        Failure::new(fsutil::write_error_code(err), format!("{what}: {err}"))
    }
}

pub(crate) enum Outcome {
    Applied {
        changed: bool,
        installed: Option<String>,
    },
    Failed(Failure),
    Blocked,
}

/// Result of one pass.
#[derive(Debug)]
pub struct PassResult {
    pub observations: Vec<ResourceObservation>,
    /// Every resource applied and everything vanished retired: the document counts as fully applied.
    pub converged: bool,
    /// Ledger entries the document dropped whose retirement failed (retried next pass).
    pub unretired: Vec<String>,
    /// Resource IDs that changed on the node in this pass.
    pub changed: BTreeSet<String>,
}

/// What the document writes, so retirement never deletes a path or unit it still uses.
struct Claims {
    paths: HashSet<String>,
    units: HashSet<String>,
}

impl Claims {
    fn of(resources: &[Resource]) -> Claims {
        let mut paths = HashSet::new();
        let mut units = HashSet::new();
        for resource in resources {
            match &resource.spec {
                Spec::File(spec) => {
                    paths.insert(spec.path.clone());
                }
                Spec::XrayConfig(spec) => {
                    paths.insert(spec.path.clone());
                }
                Spec::BinaryPin(spec) => {
                    paths.insert(spec.install_path.clone());
                }
                Spec::Unit(spec) => {
                    units.insert(spec.name.clone());
                }
                _ => {}
            }
        }
        Claims { paths, units }
    }
}

impl Engine<'_> {
    /// Converge `resources` and return one observation per resource.
    pub fn apply(
        &self,
        resources: &[Resource],
        ledger: &mut Vec<LedgerEntry>,
        previous: &[ResourceObservation],
        now: &str,
    ) -> PassResult {
        let retained = self.retire(resources, ledger);
        let mut statuses: HashMap<String, &'static str> = HashMap::new();
        let mut changed = BTreeSet::new();
        let mut placed_users: HashSet<String> = HashSet::new();
        let mut config_seen = false;
        let mut observations = Vec::with_capacity(resources.len());
        let mut entries: HashMap<String, LedgerEntry> = HashMap::new();
        for entry in ledger.drain(..) {
            entries.insert(entry.id.clone(), entry);
        }

        for resource in resources {
            let blocked = resource
                .depends_on
                .iter()
                .any(|dep| matches!(statuses.get(dep), Some(&"failed") | Some(&"blocked")));
            let previous_entry = entries
                .get(&resource.id)
                .filter(|entry| entry.kind == resource.kind);
            let outcome = if blocked {
                Outcome::Blocked
            } else {
                match &resource.spec {
                    Spec::Unsupported => Outcome::Failed(Failure::new(
                        "unsupported",
                        format!("this agent does not apply {} resources yet", resource.kind),
                    )),
                    Spec::Invalid(reason) => Outcome::Failed(Failure::new(
                        "validation_failed",
                        format!("malformed {} resource: {reason}", resource.kind),
                    )),
                    Spec::File(spec) => self.apply_file(spec, previous_entry),
                    Spec::BinaryPin(spec) => self.apply_pin(spec, previous_entry),
                    Spec::XrayConfig(spec) => {
                        config_seen = true;
                        self.apply_xray_config(
                            spec,
                            resources,
                            &statuses,
                            &entries,
                            &mut changed,
                            &mut placed_users,
                        )
                    }
                    Spec::XrayUsers(_) | Spec::Hysteria2Users(_) => users_outcome(
                        resource,
                        resources,
                        config_seen,
                        &statuses,
                        &placed_users,
                        &changed,
                    ),
                    Spec::Unit(spec) => self.apply_unit(spec, &changed, previous_entry),
                }
            };
            let (status, error) = match outcome {
                Outcome::Applied {
                    changed: did_change,
                    installed,
                } => {
                    if did_change {
                        changed.insert(resource.id.clone());
                    }
                    let keep_time = previous_entry
                        .filter(|entry| entry.applied_sha256 == resource.hash && !did_change)
                        .map(|entry| entry.changed_at.clone());
                    entries.insert(
                        resource.id.clone(),
                        LedgerEntry {
                            id: resource.id.clone(),
                            kind: resource.kind.clone(),
                            resource: resource.raw.clone(),
                            applied_sha256: resource.hash.clone(),
                            installed_sha256: installed,
                            changed_at: keep_time.unwrap_or_else(|| now.to_string()),
                        },
                    );
                    ("applied", None)
                }
                Outcome::Failed(failure) => {
                    info!(
                        "{} {}: {} ({})",
                        resource.kind, resource.id, failure.message, failure.code
                    );
                    (
                        "failed",
                        Some(ResourceError {
                            code: failure.code.to_string(),
                            message: failure.message,
                            retryable: failure.retryable,
                        }),
                    )
                }
                Outcome::Blocked => ("blocked", None),
            };
            // A unit that did not finish may owe a restart (its new config or binary is already
            // on disk): a null installed hash makes the next pass restart it once it runs.
            if matches!(resource.spec, Spec::Unit(_))
                && status != "applied"
                && let Some(entry) = entries.get_mut(&resource.id)
            {
                entry.installed_sha256 = None;
            }
            statuses.insert(resource.id.clone(), status);
            let applied_sha256 = entries
                .get(&resource.id)
                .filter(|entry| entry.kind == resource.kind)
                .map(|entry| entry.applied_sha256.clone());
            let changed_at = previous
                .iter()
                .find(|obs| {
                    obs.id == resource.id
                        && obs.status == status
                        && obs.applied_sha256 == applied_sha256
                        && obs.error == error
                })
                .filter(|_| !changed.contains(&resource.id))
                .map(|obs| obs.changed_at.clone())
                .unwrap_or_else(|| now.to_string());
            observations.push(ResourceObservation {
                id: resource.id.clone(),
                kind: resource.kind.clone(),
                status: status.to_string(),
                applied_sha256,
                error,
                changed_at,
            });
        }

        // Ledger order: leftovers whose retirement failed first, then document order.
        let unretired: Vec<String> = retained.iter().map(|entry| entry.id.clone()).collect();
        let mut next: Vec<LedgerEntry> = retained;
        for resource in resources {
            if let Some(entry) = entries.remove(&resource.id) {
                next.push(entry);
            }
        }
        *ledger = next;
        let _ = fs::remove_dir_all(&self.cache_dir);
        let converged =
            unretired.is_empty() && observations.iter().all(|obs| obs.status == "applied");
        PassResult {
            observations,
            converged,
            unretired,
            changed,
        }
    }

    // ---- Retirement ---------------------------------------------------------

    /// Retire ledger entries the document no longer names; returns entries whose retirement
    /// failed (kept for the next pass). Other ledger entries stay in `ledger`.
    fn retire(&self, resources: &[Resource], ledger: &mut Vec<LedgerEntry>) -> Vec<LedgerEntry> {
        let current: HashMap<&str, &str> = resources
            .iter()
            .map(|r| (r.id.as_str(), r.kind.as_str()))
            .collect();
        let claims = Claims::of(resources);
        let mut kept = Vec::new();
        let mut failed = Vec::new();
        for entry in ledger.drain(..).rev() {
            if current.get(entry.id.as_str()) == Some(&entry.kind.as_str()) {
                kept.push(entry);
                continue;
            }
            match self.retire_entry(&entry, &claims) {
                Ok(()) => info!("retired {} {}", entry.kind, entry.id),
                Err(err) => {
                    info!("cannot retire {} {}: {err}", entry.kind, entry.id);
                    failed.push(entry);
                }
            }
        }
        kept.reverse();
        failed.reverse();
        *ledger = kept;
        failed
    }

    fn retire_entry(&self, entry: &LedgerEntry, claims: &Claims) -> Result<(), String> {
        let field = |name: &str| {
            entry
                .resource
                .get(name)
                .and_then(Value::as_str)
                .map(str::to_string)
        };
        match entry.kind.as_str() {
            "systemd_unit" => {
                let name = field("name").ok_or("ledger entry has no unit name")?;
                if claims.units.contains(&name) {
                    return Ok(());
                }
                fsutil::check_unit_name(&name)?;
                // The unit may already be gone, so their exit codes say little: what counts is
                // that it no longer runs, checked before its file goes.
                let stopped = self.systemctl(&["stop", &name]);
                let _ = self.systemctl(&["disable", &name]);
                let state = self.query("is-active", &name);
                if matches!(
                    state.as_str(),
                    "active" | "activating" | "reloading" | "deactivating"
                ) {
                    let why = stopped.err().unwrap_or_else(|| format!("it is {state}"));
                    return Err(format!("{name} still runs: {why}"));
                }
                fsutil::remove_if_present(&self.unit_path(&name)).map_err(|err| err.to_string())?;
                self.systemctl(&["daemon-reload"]).map(|_| ())
            }
            "file" | "xray_config" | "binary_pin" => {
                let key = if entry.kind == "binary_pin" {
                    "install_path"
                } else {
                    "path"
                };
                let path = field(key).ok_or("ledger entry has no path")?;
                if claims.paths.contains(&path) {
                    return Ok(());
                }
                fsutil::check_path(&path)?;
                fsutil::remove_if_present(&host_path(&self.root, &path))
                    .map_err(|err| err.to_string())
            }
            _ => Ok(()),
        }
    }

    // ---- file -----------------------------------------------------------------

    fn apply_file(&self, spec: &FileSpec, previous: Option<&LedgerEntry>) -> Outcome {
        match self.try_file(spec, previous) {
            Ok(outcome) => outcome,
            Err(failure) => Outcome::Failed(failure),
        }
    }

    fn try_file(
        &self,
        spec: &FileSpec,
        previous: Option<&LedgerEntry>,
    ) -> Result<Outcome, Failure> {
        fsutil::check_path(&spec.path).map_err(|msg| Failure::new("path_not_allowed", msg))?;
        let mode = parse_mode(&spec.mode).map_err(|msg| Failure::new("validation_failed", msg))?;
        let uid = fsutil::lookup_id(&self.root, "passwd", &spec.owner)
            .ok_or_else(|| Failure::new("write_failed", format!("unknown user {}", spec.owner)))?;
        let gid = fsutil::lookup_id(&self.root, "group", &spec.group)
            .ok_or_else(|| Failure::new("write_failed", format!("unknown group {}", spec.group)))?;
        let dest = host_path(&self.root, &spec.path);
        let current =
            fsutil::sha256_file(&dest).map_err(|err| Failure::io(&err, "cannot read file"))?;
        if current.as_deref() == Some(spec.sha256.as_str()) {
            fsutil::ensure_mode_owner(&dest, mode, Some((uid, gid)))
                .map_err(|err| Failure::io(&err, "cannot set mode"))?;
            return Ok(Outcome::Applied {
                changed: unrecorded(previous, &current),
                installed: current,
            });
        }
        let content = match &spec.content_b64 {
            Some(encoded) => STANDARD
                .decode(encoded)
                .map_err(|_| Failure::new("validation_failed", "content_b64 is not base64"))?,
            None => {
                let mut writer = HashingWriter::new(Vec::new(), spec.size);
                self.fetch
                    .fetch("service", &spec.sha256, spec.size, &mut writer)
                    .map_err(|msg| Failure::new("download_failed", msg))?;
                writer.finish().0
            }
        };
        if content.len() as u64 != spec.size || crate::jcs::sha256_hex(&content) != spec.sha256 {
            return Err(Failure::new(
                "hash_mismatch",
                "content does not match size and sha256",
            ));
        }
        fsutil::write_atomic(&dest, &content, mode, Some((uid, gid)))
            .map_err(|err| Failure::io(&err, "cannot write file"))?;
        if current.is_some() {
            info!("repaired file {}", spec.path);
        }
        Ok(Outcome::Applied {
            changed: true,
            installed: Some(spec.sha256.clone()),
        })
    }

    // ---- binary_pin ---------------------------------------------------------

    fn apply_pin(&self, spec: &BinaryPinSpec, previous: Option<&LedgerEntry>) -> Outcome {
        match self.try_pin(spec, previous) {
            Ok(outcome) => outcome,
            Err(failure) => Outcome::Failed(failure),
        }
    }

    fn try_pin(
        &self,
        spec: &BinaryPinSpec,
        previous: Option<&LedgerEntry>,
    ) -> Result<Outcome, Failure> {
        fsutil::check_path(&spec.install_path)
            .map_err(|msg| Failure::new("path_not_allowed", msg))?;
        let mode = parse_mode(&spec.mode).map_err(|msg| Failure::new("validation_failed", msg))?;
        let dest = host_path(&self.root, &spec.install_path);
        let current =
            fsutil::sha256_file(&dest).map_err(|err| Failure::io(&err, "cannot read binary"))?;
        let same_artifact = previous.is_some_and(|entry| {
            let field = |name: &str| entry.resource.get(name).cloned().unwrap_or(Value::Null);
            field("sha256") == spec.sha256.as_str()
                && field("member") == spec.member.as_deref().map_or(Value::Null, Value::from)
                && field("install_path") == spec.install_path.as_str()
                && current.is_some()
                && entry.installed_sha256 == current
        });
        if same_artifact {
            fsutil::ensure_mode_owner(&dest, mode, None)
                .map_err(|err| Failure::io(&err, "cannot set mode"))?;
            return Ok(Outcome::Applied {
                changed: false,
                installed: current,
            });
        }
        let artifact = self.obtain(&spec.sources, &spec.sha256, spec.size)?;
        let (tmp, mut file) = fsutil::create_temp(&dest, "netagent-tmp")
            .map_err(|err| Failure::io(&err, "cannot write binary"))?;
        let extracted = extract(&artifact, &spec.archive, spec.member.as_deref(), &mut file)
            .and_then(|()| {
                file.sync_all()
                    .map_err(|err| Failure::io(&err, "cannot write binary"))
            });
        drop(file);
        if let Err(failure) = extracted {
            let _ = fs::remove_file(&tmp);
            return Err(failure);
        }
        fsutil::commit(&tmp, &dest, mode, None)
            .map_err(|err| Failure::io(&err, "cannot install binary"))?;
        let installed =
            fsutil::sha256_file(&dest).map_err(|err| Failure::io(&err, "cannot read binary"))?;
        info!("installed {} {}", spec.name, spec.version);
        Ok(Outcome::Applied {
            changed: true,
            installed,
        })
    }

    /// Download an artifact once per pass, trying sources in order; returns the verified file.
    fn obtain(&self, sources: &[String], sha256: &str, size: u64) -> Result<PathBuf, Failure> {
        let cached = self.cache_dir.join(sha256);
        if cached.is_file() {
            return Ok(cached);
        }
        fs::create_dir_all(&self.cache_dir)
            .map_err(|err| Failure::io(&err, "cannot create download cache"))?;
        let mut last = Failure::new("download_failed", "no usable source");
        for source in sources {
            let allowed = source == "service"
                || source.starts_with("https://")
                || (self.allow_http && source.starts_with("http://"));
            if !allowed {
                last = Failure::new("download_failed", "refused a source that is not https");
                continue;
            }
            let part = self.cache_dir.join(format!("{sha256}.part"));
            let file =
                File::create(&part).map_err(|err| Failure::io(&err, "cannot write download"))?;
            let mut writer = HashingWriter::new(file, size);
            let fetched = self.fetch.fetch(source, sha256, size, &mut writer);
            let (file, digest, count) = writer.finish();
            let synced = file.sync_all();
            drop(file);
            if let Err(msg) = fetched {
                debug!(
                    "download from {} failed",
                    if source == "service" {
                        "service"
                    } else {
                        "upstream"
                    }
                );
                last = Failure::new("download_failed", msg);
                let _ = fs::remove_file(&part);
                continue;
            }
            if count != size || digest != sha256 {
                last = Failure::new(
                    "hash_mismatch",
                    format!("download of {count} bytes does not match the pinned size and sha256"),
                );
                let _ = fs::remove_file(&part);
                continue;
            }
            synced.map_err(|err| Failure::io(&err, "cannot write download"))?;
            fs::rename(&part, &cached).map_err(|err| Failure::io(&err, "cannot write download"))?;
            return Ok(cached);
        }
        Err(last)
    }

    // ---- systemd_unit ---------------------------------------------------------

    fn unit_path(&self, name: &str) -> PathBuf {
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

    fn apply_unit(
        &self,
        spec: &UnitSpec,
        changed: &BTreeSet<String>,
        previous: Option<&LedgerEntry>,
    ) -> Outcome {
        // An entry without an installed hash is a unit that did not finish last time.
        let owed = previous.is_some_and(|entry| entry.installed_sha256.is_none());
        match self.try_unit(spec, changed, owed) {
            Ok(outcome) => outcome,
            Err(failure) => Outcome::Failed(failure),
        }
    }

    fn try_unit(
        &self,
        spec: &UnitSpec,
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
                let ran = self.systemctl(&[verb, &spec.name]);
                self.exec.sleep(self.settle);
                let now_active = self.query("is-active", &spec.name) == "active";
                if ran.is_err() || !now_active {
                    let journal = self.journal_tail(&spec.name);
                    return Err(service_failed(format!(
                        "{} is not active after {verb}: {journal}",
                        spec.name
                    )));
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

/// Content already on disk that the saved ledger does not record as applied: an earlier pass
/// wrote it and stopped (a crash) before saving, so whatever restarts on it has not yet.
pub(crate) fn unrecorded(previous: Option<&LedgerEntry>, installed: &Option<String>) -> bool {
    previous.is_none_or(|entry| &entry.installed_sha256 != installed)
}

/// Copy the installed file out of a downloaded artifact.
fn extract(
    artifact: &Path,
    archive: &str,
    member: Option<&str>,
    out: &mut File,
) -> Result<(), Failure> {
    let open = || File::open(artifact).map_err(|err| Failure::io(&err, "cannot open download"));
    let copy = |reader: &mut dyn Read, out: &mut File| -> Result<(), Failure> {
        let copied = io::copy(&mut reader.take(MAX_EXTRACTED_BYTES + 1), out)
            .map_err(|err| Failure::new("download_failed", format!("cannot extract: {err}")))?;
        if copied > MAX_EXTRACTED_BYTES {
            return Err(Failure::new(
                "download_failed",
                "archive member is too large",
            ));
        }
        Ok(())
    };
    match (archive, member) {
        ("none", _) => copy(&mut open()?, out),
        ("zip", Some(member)) => {
            let mut zip = zip::ZipArchive::new(open()?).map_err(|err| {
                Failure::new("download_failed", format!("not a zip archive: {err}"))
            })?;
            let mut entry = zip
                .by_name(member)
                .map_err(|_| Failure::new("download_failed", format!("archive has no {member}")))?;
            copy(&mut entry, out)
        }
        ("tar.gz", Some(member)) => {
            let mut tar = tar::Archive::new(flate2::read::GzDecoder::new(open()?));
            let entries = tar.entries().map_err(|err| {
                Failure::new("download_failed", format!("not a tar.gz archive: {err}"))
            })?;
            for entry in entries {
                let mut entry = entry.map_err(|err| {
                    Failure::new("download_failed", format!("bad tar.gz archive: {err}"))
                })?;
                let path = entry
                    .path()
                    .map(|p| p.to_string_lossy().trim_start_matches("./").to_string())
                    .unwrap_or_default();
                if path == member && entry.header().entry_type().is_file() {
                    return copy(&mut entry, out);
                }
            }
            Err(Failure::new(
                "download_failed",
                format!("archive has no {member}"),
            ))
        }
        _ => Err(Failure::new(
            "validation_failed",
            "archive and member disagree",
        )),
    }
}

pub(crate) fn last_line<'a>(stdout: &'a str, stderr: &'a str) -> &'a str {
    let last = |text: &'a str| text.lines().map(str::trim).rfind(|line| !line.is_empty());
    last(stderr).or_else(|| last(stdout)).unwrap_or("no output")
}

pub fn truncate(text: &str, max: usize) -> String {
    if text.len() <= max {
        return text.to_string();
    }
    let mut end = max;
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}…", &text[..end])
}
