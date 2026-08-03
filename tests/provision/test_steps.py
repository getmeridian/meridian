"""Tests for ProvisionContext and Provisioner core abstractions."""

from __future__ import annotations

from dataclasses import dataclass, field

from meridian.core.output import OperationContext
from meridian.core.reporters import CaptureReporter
from meridian.provision.steps import ProvisionContext, Provisioner, StepResult
from tests.support.mock_connection import MockConnection

# ---------------------------------------------------------------------------
# Mock step helpers
# ---------------------------------------------------------------------------


@dataclass
class MockStep:
    """A step that returns a preset result."""

    name: str
    status: str = "ok"
    detail: str = ""
    ran: bool = field(default=False, init=False)

    def run(self, conn, ctx) -> StepResult:
        self.ran = True
        return StepResult(name=self.name, status=self.status, detail=self.detail)


# ---------------------------------------------------------------------------
# ProvisionContext tests
# ---------------------------------------------------------------------------


class TestProvisionContextDomainMode:
    def test_domain_mode_true_when_domain_set(self):
        ctx = ProvisionContext(ip="198.51.100.1", domain="example.com")
        assert ctx.domain_mode is True

    def test_domain_mode_false_when_no_domain(self):
        ctx = ProvisionContext(ip="198.51.100.1", domain="")
        assert ctx.domain_mode is False


class TestProvisionContextNeedsWebServer:
    def test_needs_web_server_domain_mode(self):
        ctx = ProvisionContext(ip="198.51.100.1", domain="example.com")
        assert ctx.needs_web_server is True

    def test_needs_web_server_hosted_page(self):
        ctx = ProvisionContext(ip="198.51.100.1", hosted_page=True)
        assert ctx.needs_web_server is True

    def test_needs_web_server_false(self):
        ctx = ProvisionContext(ip="198.51.100.1", domain="", hosted_page=False)
        assert ctx.needs_web_server is False


class TestProvisionContextInterStepState:
    def test_path_fields_are_typed(self):
        ctx = ProvisionContext(ip="198.51.100.1")
        ctx.web_base_path = "panel"
        ctx.info_page_path = "connect"

        assert ctx.web_base_path == "panel"
        assert ctx.info_page_path == "connect"

    def test_object_fields_default_to_none(self):
        ctx = ProvisionContext(ip="198.51.100.1")
        assert ctx.panel_api is None
        assert ctx.cluster is None


class TestProvisionContextDefaults:
    def test_harden_defaults_true(self):
        ctx = ProvisionContext(ip="198.51.100.10")
        assert ctx.harden is True


# ---------------------------------------------------------------------------
# Provisioner tests
# ---------------------------------------------------------------------------


class TestProvisioner:
    def test_provisioner_stops_on_failure(self):
        step1 = MockStep(name="Step 1", status="ok")
        step2 = MockStep(name="Step 2", status="failed")
        step3 = MockStep(name="Step 3", status="ok")

        conn = MockConnection()
        ctx = ProvisionContext(ip="198.51.100.1")
        provisioner = Provisioner(steps=[step1, step2, step3])
        results = provisioner.run(conn, ctx)

        assert step1.ran is True
        assert step2.ran is True
        assert step3.ran is False
        assert len(results) == 2
        assert results[-1].status == "failed"

    def test_provisioner_collects_results(self):
        steps = [
            MockStep(name="A", status="ok"),
            MockStep(name="B", status="changed"),
            MockStep(name="C", status="skipped"),
        ]

        conn = MockConnection()
        ctx = ProvisionContext(ip="198.51.100.1")
        provisioner = Provisioner(steps=steps)
        results = provisioner.run(conn, ctx)

        assert len(results) == len(steps)
        assert [r.name for r in results] == ["A", "B", "C"]
        assert [r.status for r in results] == ["ok", "changed", "skipped"]

    def test_provisioner_appends_to_ctx_results(self):
        steps = [MockStep(name="X", status="ok")]

        conn = MockConnection()
        ctx = ProvisionContext(ip="198.51.100.1")
        assert len(ctx.results) == 0

        provisioner = Provisioner(steps=steps)
        provisioner.run(conn, ctx)

        assert len(ctx.results) == 1
        assert ctx.results[0].name == "X"

    def test_provisioner_records_duration(self):
        steps = [MockStep(name="Timed", status="ok")]

        conn = MockConnection()
        ctx = ProvisionContext(ip="198.51.100.1")
        provisioner = Provisioner(steps=steps)
        results = provisioner.run(conn, ctx)

        assert results[0].duration_ms >= 0

    def test_provisioner_emits_step_events_without_rich_rendering(self):
        steps = [MockStep(name="A", status="changed"), MockStep(name="B", status="skipped")]

        conn = MockConnection()
        ctx = ProvisionContext(ip="198.51.100.1")
        reporter = CaptureReporter()
        operation = OperationContext(operation_id="op-provision", started_at="2026-05-04T21:00:00Z")
        provisioner = Provisioner(steps=steps)

        provisioner.run(conn, ctx, reporter=reporter, operation=operation)

        assert [event.type for event in reporter.events] == [
            "provision.step.started",
            "provision.step.completed",
            "provision.step.started",
            "provision.step.completed",
        ]
        assert {event.operation_id for event in reporter.events} == {"op-provision"}
        assert reporter.events[1].data["status"] == "changed"
        assert reporter.events[3].data["status"] == "skipped"

    def test_provisioner_emits_failed_step_event(self):
        steps = [MockStep(name="A", status="failed", detail="nope"), MockStep(name="B", status="ok")]

        conn = MockConnection()
        ctx = ProvisionContext(ip="198.51.100.1")
        reporter = CaptureReporter()
        operation = OperationContext(operation_id="op-provision", started_at="2026-05-04T21:00:00Z")
        provisioner = Provisioner(steps=steps)

        provisioner.run(conn, ctx, reporter=reporter, operation=operation)

        assert [event.type for event in reporter.events] == [
            "provision.step.started",
            "provision.step.failed",
        ]
        assert reporter.events[-1].level == "error"
        assert reporter.events[-1].data["status"] == "failed"
        assert reporter.events[-1].data["detail"] == "nope"
