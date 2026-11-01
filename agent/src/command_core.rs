//! One preflight for commands that can restart the core or reset its inbounds.

use serde_json::Value;

use crate::apply::{Engine, Failure};
use crate::commands::CommandAction;
use crate::compose::{self, UsersRef};
use crate::fsutil;
use crate::model::{Resource, Spec, check_loopback};
use crate::xray_health::CORE_UNIT;

pub(crate) fn touches_core(action: &CommandAction, resources: &[Resource]) -> bool {
    match action {
        CommandAction::ResetInbound { .. } => true,
        CommandAction::RestartUnit { resource_id } => resources.iter().any(|resource| {
            resource.id == *resource_id
                && matches!(&resource.spec, Spec::Unit(unit) if unit.name == CORE_UNIT)
        }),
        _ => false,
    }
}

impl Engine<'_> {
    pub(crate) fn command_core_configuration(
        &self,
        resources: &[Resource],
    ) -> Result<Value, Failure> {
        let invalid = |message: &str| Failure::new("validation_failed", message);
        let pending = |code, message: &str| Failure::new(code, message);
        let config = resources
            .iter()
            .find_map(|resource| match &resource.spec {
                Spec::XrayConfig(config) => Some(config),
                _ => None,
            })
            .ok_or_else(|| {
                pending(
                    "document_not_applied",
                    "This node has no held Xray configuration. Apply the fleet and retry.",
                )
            })?;
        fsutil::check_path(&config.path)
            .map_err(|_| invalid("The Xray config path is not allowed."))?;
        check_loopback(&config.api_address)
            .map_err(|_| invalid("The Xray API address is not loopback."))?;
        let users: Vec<_> = resources
            .iter()
            .filter_map(|resource| match &resource.spec {
                Spec::XrayUsers(users) => Some(UsersRef::Vless(users)),
                Spec::Hysteria2Users(users) => Some(UsersRef::Hysteria2(users)),
                _ => None,
            })
            .collect();
        let composed = compose::compose(&config.config, &users)
            .map_err(|_| invalid("The held Xray configuration cannot be composed."))?;
        let installed =
            std::fs::read(fsutil::host_path(&self.root, &config.path)).map_err(|_| {
                pending(
                    "config_not_installed",
                    "The held Xray configuration is not installed. Apply the fleet and retry.",
                )
            })?;
        if installed != compose::render(&composed) {
            return Err(pending(
                "config_mismatch",
                "The installed Xray configuration differs from the held document. Apply the fleet and retry.",
            ));
        }
        Ok(composed)
    }
}
