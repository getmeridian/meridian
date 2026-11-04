"""Shared committed revision and apply-fence fixtures."""

from meridian.control.models import ApplyFenceRecord, ApplyFenceSwap, RevisionProposal, RevisionResult
from meridian.control.service import ControlService
from meridian.control.tokens import ROOT
from meridian.core.topology import SetupIntent


def commit_topology(service: ControlService, intent: SetupIntent, *, generation: int = 1, plan_hash: str = "a" * 64):
    revision = service.revisions.propose(
        RevisionProposal(intent=intent.model_dump(mode="json"), plan_hash=plan_hash, review_hash="b" * 64), caller=ROOT
    )
    service.revisions.approve(revision.number, caller=ROOT)
    service.revisions.record_result(revision.number, RevisionResult(status="applied"))
    current = service.revisions.fence()
    service.revisions.swap_fence(
        ApplyFenceSwap(
            expected_version=current.version,
            record=ApplyFenceRecord(
                fleet_id=service.fleet_id, generation=generation, plan_hash=plan_hash, revision=revision.number
            ),
        )
    )
    return revision
