//! Report construction and delivery, separate from polling and application.

use std::collections::HashMap;

use super::Agent;
use crate::apply::Engine;
use crate::fetch::HttpError;
use crate::model::{ObservedStateReport, ReportAck, ResourceError, Spec, UserFlowBlocking};
use crate::report;
use crate::timeutil::now_rfc3339;

impl Agent<'_> {
    pub fn build_report(&self) -> ObservedStateReport {
        let collection: Vec<_> = crate::xray_health::wanted(&self.resources)
            .then(|| self.collection_health.check(self.exec.now()))
            .into_iter()
            .collect();
        let user_flow_blocking = self
            .session
            .as_ref()
            .filter(|session| session.uses("user_flow_blocking"))
            .and_then(|_| self.state.applied.clone().or_else(|| self.state.held_ref()))
            .map(|document| {
                let engine = Engine {
                    root: self.opts.root.clone(),
                    cache_dir: self.opts.state_dir.join("cache"),
                    exec: self.exec,
                    fetch: &self.client,
                    settle: self.opts.settle,
                    allow_http: self.opts.allow_http,
                };
                let statuses: HashMap<_, _> = self
                    .observations
                    .iter()
                    .map(|observation| {
                        (
                            observation.id.clone(),
                            if observation.status == "applied" {
                                "applied"
                            } else {
                                "pending"
                            },
                        )
                    })
                    .collect();
                let mut error = engine.flow_guard_observation(&self.resources, &statuses);
                let managed = self.resources.iter().any(|resource| {
                    matches!(resource.spec, Spec::XrayUsers(_) | Spec::Hysteria2Users(_))
                });
                if error.is_none()
                    && (!managed || self.state.applied.is_none())
                    && self.state.applied != self.state.held_ref()
                {
                    error = Some(ResourceError {
                        code: "apply_pending".into(),
                        message: "The node has not applied its held document.".into(),
                        retryable: true,
                    });
                }
                UserFlowBlocking {
                    document,
                    status: if error.is_some() { "pending" } else { "ready" },
                    error,
                }
            });
        ObservedStateReport {
            kind: "report",
            fleet_id: self.bootstrap.fleet_id.clone(),
            node_id: self.bootstrap.node_id.clone(),
            applied: self.state.applied.clone(),
            target: self.state.held_ref(),
            resources: self.observations.clone(),
            health: report::health_with_checks(
                &self.opts.root,
                &self.opts.state_dir,
                self.exec,
                &self.resources,
                &self.observations,
                &collection,
            ),
            versions: report::versions(&self.opts.root, &self.resources, &self.observations),
            observed_at: now_rfc3339(),
            user_flow_blocking,
        }
    }

    pub fn send_report(&mut self) -> Result<(), HttpError> {
        self.connect()?;
        let report = self.build_report();
        if let Some(guard) = &report.user_flow_blocking {
            self.flow_error = guard.error.clone();
        }
        let body = self.with_session(|client, session| {
            let mut report = report.clone();
            if !session.uses("user_flow_blocking") {
                report.user_flow_blocking = None;
            }
            client.call("report", &report)
        })?;
        super::typed::<ReportAck>(body, "report_ack").map(|_| ())
    }
}
