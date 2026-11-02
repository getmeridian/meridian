"""The deployer's committed revision and username guards for a people-removal draft."""

from collections.abc import Collection

from meridian.control.models import RevisionRecord
from meridian.core.people_removal import PeopleRemovalProposal
from meridian.i18n import isolate, t
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


def require_removal_users(usernames: Collection[str], declared: Collection[str]) -> None:
    """Removal targets must be exact declarations after any named-command resolution."""
    unknown = sorted(set(usernames) - set(declared))
    if unknown:
        raise FleetFenceError(
            t("bulk.remove_undeclared", people=", ".join(isolate(name) for name in unknown)), category="user"
        )


def require_removal_revision(proposal: PeopleRemovalProposal, revision: RevisionRecord | None) -> None:
    """Check the recorded intent, including its exact usernames, before a removal can proceed."""
    if (
        revision is None
        or revision.status != "applied"
        or revision.intent_sha256 != proposal.intent_sha256
        or revision.plan_hash != proposal.plan_hash
    ):
        raise FleetFenceError(t("bulk.proposal_stale"), category="user")
    require_removal_users(proposal.usernames, revision.intent.get("access", {}).get("users", []))
