"""Declared deletion drafts use the committed apply fence, without widening owner routes."""

from meridian.control.errors import ServiceError
from meridian.control.revisions import RevisionLedger
from meridian.core.people_removal import PeopleRemovalProposal


def removal_proposal(revisions: RevisionLedger, usernames: list[str]) -> PeopleRemovalProposal | None:
    if not usernames:
        return None
    state = revisions.fence()
    record = state.record
    if record is None or record.revision is None or not record.plan_hash or record.generation < 1:
        raise ServiceError(
            "topology_uncommitted",
            "The service has no committed topology for this removal. Finish meridian apply, then preview again.",
            409,
        )
    revision = revisions.get(record.revision)
    if (
        revision.status != "applied"
        or revision.plan_hash != record.plan_hash
        or not set(usernames) <= set(revision.intent.get("access", {}).get("users", []))
    ):
        raise ServiceError(
            "topology_uncommitted",
            "These declared people are not in a committed topology. Finish meridian apply, then preview again.",
            409,
        )
    return PeopleRemovalProposal(
        fleet_id=state.fleet_id,
        revision=revision.number,
        generation=record.generation,
        plan_hash=record.plan_hash,
        intent_sha256=revision.intent_sha256,
        usernames=usernames,
    )


def require_current_proposal(revisions: RevisionLedger, expected: PeopleRemovalProposal) -> None:
    try:
        current = removal_proposal(revisions, expected.usernames)
    except ServiceError as exc:
        if exc.code != "topology_uncommitted":
            raise
        current = None
    if current != expected:
        raise ServiceError(
            "stale_topology", "The committed topology changed since this removal was reviewed. Preview again.", 409
        )
