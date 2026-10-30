//! Signed command admission and durable results (`commands.json`, 0600).
//! A saved interrupted-outcome result precedes execution, so a crash cannot
//! make a redelivery repeat a restart whose outcome was never saved.

use std::collections::HashSet;
use std::io::ErrorKind;
use std::path::Path;

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::model::{ResourceError, ServiceSignature, check_tag};
use crate::timeutil::{format_unix, parse_rfc3339};
use crate::verify::{Trust, verify_signature_in_context};

pub const COMMAND_CONTEXT: &[u8] = b"meridian.agent/v1 command";
pub const COMMAND_STATE_FILE: &str = "commands.json";
pub const MAX_REMEMBERED: usize = 1024;
const MAX_SAFE_INTEGER: u64 = (1 << 53) - 1;

#[derive(Deserialize, Debug)]
pub struct CommandHeader {
    pub command_id: String,
    pub fleet_epoch: u64,
    pub issued_at: String,
    pub expires_at: String,
    pub signature: ServiceSignature,
}

#[derive(Deserialize, Debug)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum CommandAction {
    Reconcile {},
    Report { include_stats: bool },
    ResetInbound { inbound_tag: String },
    RestartUnit { resource_id: String },
    RenewCredential {},
}

impl CommandAction {
    pub fn kind(&self) -> &'static str {
        match self {
            Self::Reconcile {} => "reconcile",
            Self::Report { .. } => "report",
            Self::ResetInbound { .. } => "reset_inbound",
            Self::RestartUnit { .. } => "restart_unit",
            Self::RenewCredential {} => "renew_credential",
        }
    }
}

#[derive(Debug)]
pub struct VerifiedCommand {
    pub header: CommandHeader,
    pub action: CommandAction,
    pub digest: String,
    pub expires_at: u64,
}

pub fn is_command_id(text: &str) -> bool {
    text.len() == 36
        && text.bytes().enumerate().all(|(index, byte)| {
            if [8, 13, 18, 23].contains(&index) {
                byte == b'-'
            } else {
                byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)
            }
        })
}

fn command_time(text: &str) -> Option<u64> {
    let seconds = parse_rfc3339(text)?;
    // The protocol permits at most nine fractional digits and requires a real calendar date.
    (text.len() <= 30 && format_unix(seconds).get(..19) == text.get(..19)).then_some(seconds)
}

/// Verify the received JSON before typing it; errors never include hostile field values.
pub fn verify_command(
    value: &Value,
    trust: &Trust,
    highest_epoch: Option<u64>,
    session_epoch: u64,
    poll_epoch: u64,
    now: u64,
    capabilities: &[String],
) -> Result<VerifiedCommand, &'static str> {
    crate::jcs::check_no_floats(value)
        .map_err(|_| "The command contains a floating-point number.")?;
    verify_signature_in_context(value, &trust.public_key, &trust.key_id, COMMAND_CONTEXT).map_err(
        |_| "The command signature does not verify under this fleet's service certificate.",
    )?;
    let header: CommandHeader =
        serde_json::from_value(value.clone()).map_err(|_| "The command header is malformed.")?;
    if !is_command_id(&header.command_id) {
        return Err("The command ID is malformed.");
    }
    if header.fleet_epoch == 0
        || header.fleet_epoch > MAX_SAFE_INTEGER
        || header.fleet_epoch < highest_epoch.unwrap_or(1)
        || header.fleet_epoch != session_epoch
        || header.fleet_epoch != poll_epoch
    {
        return Err("The command does not belong to the current fleet epoch.");
    }
    let issued = command_time(&header.issued_at).ok_or("The command issue time is invalid.")?;
    let expires_at = command_time(&header.expires_at).ok_or("The command expiry is invalid.")?;
    if issued > now.saturating_add(300) || expires_at <= issued {
        return Err("The command's validity window is invalid.");
    }
    let mut fields = value
        .as_object()
        .ok_or("The command is not an object.")?
        .clone();
    for field in [
        "command_id",
        "fleet_epoch",
        "issued_at",
        "expires_at",
        "signature",
    ] {
        fields.remove(field);
    }
    let action: CommandAction = serde_json::from_value(Value::Object(fields))
        .map_err(|_| "The command kind or its fields are unsupported.")?;
    if !capabilities.iter().any(|name| name == action.kind()) {
        return Err("The session did not negotiate this command kind.");
    }
    match &action {
        CommandAction::ResetInbound { inbound_tag } => {
            check_tag(inbound_tag).map_err(|_| "The inbound tag is invalid.")?;
        }
        CommandAction::RestartUnit { resource_id } => {
            let allowed = |b: u8| b.is_ascii_lowercase() || b.is_ascii_digit();
            let mut bytes = resource_id.bytes();
            if resource_id.len() > 128
                || !bytes.next().is_some_and(allowed)
                || !bytes.all(|b| allowed(b) || b"._:-".contains(&b))
            {
                return Err("The unit resource ID is invalid.");
            }
        }
        _ => {}
    }
    let digest = crate::jcs::canonical_sha256(value).map_err(|_| "The command JSON is invalid.")?;
    Ok(VerifiedCommand {
        header,
        action,
        digest,
        expires_at,
    })
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CommandStatus {
    Succeeded,
    Failed,
    Rejected,
    Expired,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CommandEffect {
    InboundReset,
    CoreRestart,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct CommandResult {
    #[serde(rename = "type")]
    pub kind: String,
    pub command_id: String,
    pub status: CommandStatus,
    pub error: Option<ResourceError>,
    pub finished_at: String,
    pub effect: Option<CommandEffect>,
}

impl CommandResult {
    pub fn new(command_id: &str, status: CommandStatus, now: u64) -> Self {
        Self {
            kind: "command_result".into(),
            command_id: command_id.into(),
            status,
            error: None,
            finished_at: format_unix(now),
            effect: None,
        }
    }

    pub fn failure(
        command_id: &str,
        status: CommandStatus,
        code: &str,
        message: &str,
        now: u64,
    ) -> Self {
        let mut result = Self::new(command_id, status, now);
        result.error = Some(ResourceError {
            code: code.into(),
            message: message.chars().take(300).collect(),
            retryable: false,
        });
        result
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CommandResultAck {
    #[serde(rename = "type")]
    pub kind: String,
    pub command_id: String,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(deny_unknown_fields)]
pub struct RememberedCommand {
    pub digest: String,
    pub expires_at: u64,
    pub result: CommandResult,
    pub acknowledged: bool,
}

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
#[serde(deny_unknown_fields)]
pub struct CommandState {
    pub results: Vec<RememberedCommand>,
}

impl CommandState {
    pub fn load(state_dir: &Path) -> Result<Self, String> {
        let bytes = match std::fs::read(state_dir.join(COMMAND_STATE_FILE)) {
            Ok(bytes) => bytes,
            Err(error) if error.kind() == ErrorKind::NotFound => return Ok(Self::default()),
            Err(_) => return Err("Cannot read commands.json in the agent state directory.".into()),
        };
        let state: Self =
            serde_json::from_slice(&bytes).map_err(|_| "commands.json is malformed.")?;
        let mut seen = HashSet::new();
        if state.results.len() > MAX_REMEMBERED
            || state.results.iter().any(|entry| {
                !is_command_id(&entry.result.command_id)
                    || !seen.insert(&entry.result.command_id)
                    || !crate::jcs::is_sha256_hex(&entry.digest)
                    || entry.result.kind != "command_result"
                    || parse_rfc3339(&entry.result.finished_at).is_none()
                    || entry.result.error.is_some()
                        != matches!(
                            entry.result.status,
                            CommandStatus::Failed | CommandStatus::Rejected
                        )
                    || (entry.result.effect.is_some()
                        && entry.result.status != CommandStatus::Succeeded)
            })
        {
            return Err("commands.json has invalid or duplicate results.".into());
        }
        Ok(state)
    }

    pub fn save(&self, state_dir: &Path) -> Result<(), String> {
        std::fs::create_dir_all(state_dir)
            .map_err(|_| "Cannot create the agent state directory.")?;
        let bytes = serde_json::to_vec(self).map_err(|_| "Cannot serialize command results.")?;
        crate::fsutil::write_atomic(&state_dir.join(COMMAND_STATE_FILE), &bytes, 0o600, None)
            .map_err(|_| {
                "Cannot save commands.json; no command will run without a durable result.".into()
            })
    }

    pub fn prune(&mut self, now: u64) {
        self.results.retain(|entry| entry.expires_at > now);
    }

    pub fn find(&self, id: &str) -> Option<&RememberedCommand> {
        self.results
            .iter()
            .find(|entry| entry.result.command_id == id)
    }

    pub fn remember(&mut self, entry: RememberedCommand) -> Result<(), String> {
        if let Some(previous) = self
            .results
            .iter_mut()
            .find(|item| item.result.command_id == entry.result.command_id)
        {
            *previous = entry;
        } else if self.results.len() < MAX_REMEMBERED {
            self.results.push(entry);
        } else {
            return Err("The command result store is full; wait for commands to expire.".into());
        }
        Ok(())
    }
}
