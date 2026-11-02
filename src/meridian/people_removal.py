"""The deployer's exact revision precondition for a declared-people removal draft."""

from meridian.core.people_removal import PeopleRemovalProposal
from meridian.i18n import t
from meridian.reconciler.fence import FleetFenceError


def require_removal_fence(
    proposal: PeopleRemovalProposal,
    *,
    fleet_id: str,
    generation: int | None,
    plan_hash: str | None,
    revision: int | None,
) -> None:
    expected = (proposal.fleet_id, proposal.generation, proposal.plan_hash, proposal.revision)
    if (fleet_id, generation, plan_hash, revision) != expected:
        raise FleetFenceError(t("bulk.proposal_stale"), category="user")
