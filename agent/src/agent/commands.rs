//! Command orchestration stays outside the polling loop; only verified commands reach host actions.

use serde_json::Value;

use super::Agent;
use crate::apply::{Engine, Failure};
use crate::commands::{
    CommandAction, CommandEffect, CommandResult, CommandResultAck, CommandStatus,
    RememberedCommand, is_command_id, verify_command,
};
use crate::fetch::HttpError;
use crate::session;
use crate::timeutil::now_unix;

impl Agent<'_> {
    fn command_now(&self) -> u64 {
        now_unix().saturating_add_signed(self.server_offset)
    }

    pub(super) fn retry_command_results(&mut self) -> Result<(), HttpError> {
        self.connect()?;
        let mut next = self.command_state.clone();
        next.prune(self.command_now());
        if next.results.len() != self.command_state.results.len() {
            next.save(&self.opts.state_dir)
                .map_err(HttpError::Protocol)?;
            self.command_state = next;
        }
        let pending: Vec<_> = self
            .command_state
            .results
            .iter()
            .filter(|entry| !entry.acknowledged)
            .map(|entry| entry.result.clone())
            .collect();
        for result in pending {
            match self.send_command_result(&result) {
                // A restore forgets transient commands. Retain the result, but keep polling.
                Err(HttpError::Status {
                    status: 400,
                    code: Some(code),
                    ..
                }) if code == "invalid_request" => {}
                Err(HttpError::Status { status: 404, .. }) => {}
                other => other?,
            }
        }
        Ok(())
    }

    fn send_command_result(&mut self, result: &CommandResult) -> Result<(), HttpError> {
        let body = self.with_session(|client, _| client.call("command-results", result))?;
        let ack: CommandResultAck = super::typed(body, "command_result_ack")?;
        if ack.command_id != result.command_id {
            return Err(HttpError::Protocol(
                "The command acknowledgement names another command.".into(),
            ));
        }
        let mut next = self.command_state.clone();
        if let Some(entry) = next
            .results
            .iter_mut()
            .find(|entry| entry.result.command_id == result.command_id)
            && entry.result == *result
        {
            entry.acknowledged = true;
            next.save(&self.opts.state_dir)
                .map_err(HttpError::Protocol)?;
            self.command_state = next;
        }
        Ok(())
    }

    pub(super) fn process_commands(
        &mut self,
        values: &[Value],
        poll_epoch: u64,
    ) -> Result<(), HttpError> {
        if values.len() > 64 {
            return Err(HttpError::Protocol(
                "A poll returned more than 64 commands.".into(),
            ));
        }
        for value in values {
            self.process_command(value, poll_epoch)?;
        }
        Ok(())
    }

    fn reject_command(&mut self, value: &Value, id: &str, message: &str) -> Result<(), HttpError> {
        let now = self.command_now();
        let result = CommandResult::failure(
            id,
            CommandStatus::Rejected,
            "validation_failed",
            message,
            now,
        );
        if self.command_state.find(id).is_none() {
            let digest = crate::jcs::canonical_sha256(value)
                .unwrap_or_else(|_| crate::jcs::sha256_hex(b"rejected"));
            let mut next = self.command_state.clone();
            if next
                .remember(RememberedCommand {
                    digest,
                    expires_at: now + 300,
                    result: result.clone(),
                    acknowledged: false,
                })
                .is_ok()
            {
                next.save(&self.opts.state_dir)
                    .map_err(HttpError::Protocol)?;
                self.command_state = next;
            }
        }
        self.send_command_result(&result)
    }

    fn process_command(&mut self, value: &Value, poll_epoch: u64) -> Result<(), HttpError> {
        if self.session.is_none() {
            self.open_session()?;
        }
        let id = value
            .get("command_id")
            .and_then(Value::as_str)
            .filter(|id| is_command_id(id))
            .ok_or_else(|| {
                HttpError::Protocol("An unverifiable command has no valid command ID.".into())
            })?
            .to_string();
        let trust = self.trust.as_ref().ok_or_else(|| {
            HttpError::Protocol("No fleet signing certificate is trusted.".into())
        })?;
        let session = self.session.as_ref().ok_or_else(|| {
            HttpError::Protocol("No command session could be opened; retry the poll.".into())
        })?;
        let verified = match verify_command(
            value,
            trust,
            self.state.highest_epoch,
            session.fleet_epoch,
            poll_epoch,
            self.command_now(),
            &session.capabilities,
        ) {
            Ok(command) => command,
            Err(message) => return self.reject_command(value, &id, message),
        };
        let now = self.command_now();
        if let Some(remembered) = self.command_state.find(&id) {
            if remembered.digest != verified.digest {
                return self.reject_command(
                    value,
                    &id,
                    "This command ID was already used for different content.",
                );
            }
            let result = remembered.result.clone();
            return self.send_command_result(&result);
        }
        let expires_at = verified.expires_at;
        let mut result = if expires_at <= now {
            CommandResult::new(&id, CommandStatus::Expired, now)
        } else {
            CommandResult::failure(
                &id,
                CommandStatus::Failed,
                "internal",
                "The agent stopped before this command's outcome was saved; inspect the node.",
                now,
            )
        };
        let mut next = self.command_state.clone();
        if next
            .remember(RememberedCommand {
                digest: verified.digest.clone(),
                expires_at,
                result: result.clone(),
                acknowledged: false,
            })
            .is_err()
        {
            return self.reject_command(
                value,
                &id,
                "The command result store is full; wait for retained results to age out.",
            );
        }
        next.save(&self.opts.state_dir)
            .map_err(HttpError::Protocol)?;
        self.command_state = next;
        if expires_at > self.command_now() {
            let mut state = self.state.clone();
            state.highest_epoch = Some(
                state
                    .highest_epoch
                    .unwrap_or(0)
                    .max(verified.header.fleet_epoch),
            );
            state
                .save(&self.opts.state_dir)
                .map_err(HttpError::Protocol)?;
            self.state = state;
            let outcome = (expires_at > self.command_now())
                .then(|| self.execute_command(&verified.action, verified.header.fleet_epoch));
            let now = self.command_now();
            result = match outcome {
                Some(Ok(effect)) => {
                    let mut result = CommandResult::new(&id, CommandStatus::Succeeded, now);
                    result.effect = effect;
                    result
                }
                Some(Err(failure)) => {
                    let status = if matches!(
                        failure.code,
                        "validation_failed" | "unsupported" | "path_not_allowed"
                    ) {
                        CommandStatus::Rejected
                    } else {
                        CommandStatus::Failed
                    };
                    let mut result =
                        CommandResult::failure(&id, status, failure.code, &failure.message, now);
                    if let Some(error) = &mut result.error {
                        error.retryable = failure.retryable;
                    }
                    result
                }
                None => CommandResult::new(&id, CommandStatus::Expired, now),
            };
        } else {
            result = CommandResult::new(&id, CommandStatus::Expired, self.command_now());
        }
        let mut next = self.command_state.clone();
        next.remember(RememberedCommand {
            digest: verified.digest,
            expires_at,
            result: result.clone(),
            acknowledged: false,
        })
        .map_err(HttpError::Protocol)?;
        next.save(&self.opts.state_dir)
            .map_err(HttpError::Protocol)?;
        self.command_state = next;
        self.send_command_result(&result)
    }

    fn execute_command(
        &mut self,
        action: &CommandAction,
        epoch: u64,
    ) -> Result<Option<CommandEffect>, Failure> {
        let failed = |message: &str| Failure::new("service_failed", message);
        match action {
            CommandAction::Report { include_stats } => {
                if *include_stats {
                    if !self
                        .session
                        .as_ref()
                        .is_some_and(|s| s.uses("traffic_stats"))
                    {
                        return Err(Failure::new(
                            "unsupported",
                            "The session did not negotiate traffic statistics.",
                        ));
                    }
                    self.send_stats()
                        .map_err(|_| failed("Traffic statistics could not be sent."))?;
                }
                self.send_report()
                    .map_err(|_| failed("The node report could not be sent."))?;
            }
            CommandAction::Reconcile {} => {
                if self.state.held_ref().is_none() {
                    return Err(Failure::new(
                        "validation_failed",
                        "This node has no held desired state.",
                    ));
                }
                self.apply_held()
                    .map_err(|_| failed("The reconciliation pass could not be saved."))?;
                if self.state.applied != self.state.held_ref()
                    || self
                        .observations
                        .iter()
                        .any(|item| item.status != "applied")
                {
                    return Err(failed(
                        "The reconciliation pass did not apply every resource.",
                    ));
                }
                self.send_report()
                    .map_err(|_| failed("The reconciliation report could not be sent."))?;
            }
            CommandAction::RenewCredential {} => {
                self.renew_now()
                    .map_err(|_| failed("The node credential could not be renewed and saved."))?;
            }
            CommandAction::RestartUnit { .. } | CommandAction::ResetInbound { .. } => {
                if !self
                    .state
                    .held_ref()
                    .is_some_and(|held| held.fleet_epoch == epoch)
                {
                    return Err(Failure::new(
                        "validation_failed",
                        "This node has no held document from the command's epoch.",
                    ));
                }
                if crate::command_core::touches_core(action, &self.resources)
                    && self.state.applied != self.state.held_ref()
                {
                    return Err(Failure::new(
                        "document_not_applied",
                        "Apply the held document before restarting or resetting the core.",
                    ));
                }
                // Fold traffic before a reset can discard counters; a service outage never postpones a cut-off.
                let _ = self.send_stats();
                let engine = Engine {
                    root: self.opts.root.clone(),
                    cache_dir: self.opts.state_dir.join("cache"),
                    exec: self.exec,
                    fetch: &self.client,
                    settle: self.opts.settle,
                    allow_http: self.opts.allow_http,
                };
                match action {
                    CommandAction::RestartUnit { resource_id } => {
                        engine.restart_for_command(resource_id, &self.resources)?
                    }
                    CommandAction::ResetInbound { inbound_tag } => {
                        return engine
                            .reset_for_command(inbound_tag, &self.resources)
                            .map(Some);
                    }
                    _ => unreachable!(),
                }
            }
        }
        Ok(None)
    }

    pub(super) fn renew_now(&mut self) -> Result<(), HttpError> {
        self.open_session()?;
        let bootstrap = self.bootstrap.clone();
        let identity = self
            .identity
            .as_ref()
            .ok_or_else(|| HttpError::Protocol("This node is not enrolled.".into()))?
            .clone();
        let mut renewed =
            self.with_session(|client, _| session::renew(client, &bootstrap, &identity))?;
        // A retrying hello may have refreshed the signing certificate.
        if let Some(current) = &self.identity {
            renewed.service = current.service.clone();
        }
        renewed
            .save(&self.opts.state_dir)
            .map_err(HttpError::Protocol)?;
        self.identity = Some(renewed);
        if let Some(session) = &mut self.session {
            session.renew_after = session.expires_at;
        }
        Ok(())
    }
}
