//! Report construction and delivery, separate from polling and application.

use super::Agent;
use crate::fetch::HttpError;
use crate::model::{ObservedStateReport, ReportAck};
use crate::report;
use crate::timeutil::now_rfc3339;

impl Agent<'_> {
    pub fn build_report(&self) -> ObservedStateReport {
        let collection: Vec<_> = crate::xray_health::wanted(&self.resources)
            .then(|| self.collection_health.check(self.exec.now()))
            .into_iter()
            .collect();
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
        }
    }

    pub fn send_report(&mut self) -> Result<(), HttpError> {
        let report = self.build_report();
        let body = self.with_session(|client, _| client.call("report", &report))?;
        super::typed::<ReportAck>(body, "report_ack").map(|_| ())
    }
}
