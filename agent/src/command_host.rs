//! Host actions reuse composition and the existing start-and-check readiness path.

use crate::apply::{Engine, Failure};
use crate::commands::CommandEffect;
use crate::fsutil;
use crate::model::{Resource, Spec, check_tag};
use crate::xray_health::{self, CORE_UNIT};

impl Engine<'_> {
    pub(crate) fn restart_for_command(
        &self,
        id: &str,
        resources: &[Resource],
    ) -> Result<(), Failure> {
        let spec = resources
            .iter()
            .find_map(|resource| match &resource.spec {
                Spec::Unit(unit) if resource.id == id => Some(unit),
                _ => None,
            })
            .ok_or_else(|| {
                Failure::new(
                    "validation_failed",
                    "This node does not manage that unit resource.",
                )
            })?;
        fsutil::check_unit_name(&spec.name).map_err(|_| {
            Failure::new("validation_failed", "The command cannot restart that unit.")
        })?;
        if spec.state != "started" {
            return Err(Failure::new(
                "validation_failed",
                "The held document keeps that unit stopped.",
            ));
        }
        let content = std::fs::read(self.unit_path(&spec.name)).map_err(|_| {
            Failure::new(
                "validation_failed",
                "The managed unit is not installed; reconcile first.",
            )
        })?;
        if content != spec.content.as_bytes() {
            return Err(Failure::new(
                "validation_failed",
                "The managed unit file differs from the held document; reconcile first.",
            ));
        }
        if spec.name == CORE_UNIT {
            self.command_core_configuration(resources)?;
        }
        self.start_and_check(spec, resources, "restart")
            .map_err(|_| {
                Failure::new(
                    "service_failed",
                    "The managed unit did not become ready after restart.",
                )
            })
    }

    pub(crate) fn reset_for_command(
        &self,
        tag: &str,
        resources: &[Resource],
    ) -> Result<CommandEffect, Failure> {
        let invalid = |message: &str| Failure::new("validation_failed", message);
        check_tag(tag).map_err(|_| invalid("The inbound tag is invalid."))?;
        let managed = resources.iter().any(|resource| match &resource.spec {
            Spec::XrayUsers(users) => users.inbound_tag == tag,
            Spec::Hysteria2Users(users) => users.inbound_tag == tag,
            _ => false,
        });
        if !managed {
            return Err(invalid("This node does not manage users on that inbound."));
        }
        let composed = self.command_core_configuration(resources)?;
        let pin = resources
            .iter()
            .find_map(|resource| match &resource.spec {
                Spec::BinaryPin(pin) if pin.name == "xray" => Some(pin),
                _ => None,
            })
            .ok_or_else(|| invalid("This node has no managed Xray binary."))?;
        fsutil::check_path(&pin.install_path)
            .map_err(|_| invalid("The Xray binary path is not allowed."))?;
        let (binary, api) = xray_health::api(&self.root, resources)
            .ok_or_else(|| invalid("The Xray API is not configured or installed."))?;
        let inbound = crate::xray::composed_inbound(&composed, tag)
            .map_err(|_| invalid("The composed Xray configuration has no such inbound."))?;
        let server = format!("--server={api}");
        if self
            .rebuild_inbound(&binary, &server, tag, &inbound)
            .is_ok()
            && xray_health::wait_ready(&self.root, self.exec, resources).is_ok()
        {
            return Ok(CommandEffect::InboundReset);
        }
        let core = resources
            .iter()
            .find(|resource| {
                matches!(&resource.spec,
            Spec::Unit(unit) if unit.name == CORE_UNIT && unit.state == "started")
            })
            .ok_or_else(|| {
                Failure::new(
                    "service_failed",
                    "The inbound reset failed and this node has no managed core to restart.",
                )
            })?;
        self.restart_for_command(&core.id, resources)
            .map_err(|failure| {
                if failure.code == "apply_pending" {
                    return failure;
                }
                Failure::new(
                    "service_failed",
                    "The inbound reset failed and the fallback core restart did not become ready.",
                )
            })?;
        Ok(CommandEffect::CoreRestart)
    }
}
