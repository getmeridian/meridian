"""Read-only effective access, gain/loss reviews, and shared-listener cut-off scope."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from meridian.control.access_memberships import (
    eligible_endpoints,
    profile_access_hash,
    read_access_profiles,
    refuse_used_profiles,
)
from meridian.control.command_models import CutOffNode, CutOffPlan
from meridian.control.formats.entries import presentation_pairs
from meridian.control.models import ClientEndpoint, NodeSpec, UserRecord
from meridian.control.profile_declarations import has_non_default_memberships, read_profile_declarations_hash
from meridian.control.store import Store
from meridian.core.access_profile_views import (
    AccessChange,
    AccessProfileCatalog,
    AccessProfileReview,
    AccessProfileReviewRequest,
    AccessProfileSummary,
    GrantedConnection,
    GrantedInbound,
    PersonAccess,
)
from meridian.core.access_profiles import DEFAULT_ACCESS_PROFILE, access_reasons, may_use_inbound


class AccessProfileLedger:
    def __init__(
        self, store: Store, *, read_user: Callable[[str], UserRecord], read_users: Callable[[], list[UserRecord]]
    ) -> None:
        self.store, self.read_user, self.read_users = store, read_user, read_users

    def inbounds(self) -> list[GrantedInbound]:
        return [
            GrantedInbound(
                node_id=row["node_id"], inbound_tag=inbound.tag, inbound_ref=inbound.inbound_ref, granted_by=[]
            )
            for row in self.store.rows("SELECT node_id, spec FROM nodes WHERE spec IS NOT NULL ORDER BY node_id")
            for inbound in NodeSpec.model_validate_json(row["spec"]).managed_inbounds
        ]

    def person(self, username: str) -> PersonAccess:
        with self.store.lock:
            user, profiles = self.read_user(username), read_access_profiles(self.store)
            inbounds = [
                GrantedInbound.model_validate({**inbound.to_data(), "granted_by": reasons})
                for inbound in self.inbounds()
                if (reasons := access_reasons(user.profiles, profiles, inbound.inbound_ref))
            ]
            connections = []
            rows = self.store.rows("SELECT endpoint_id, body FROM endpoints ORDER BY endpoint_id")
            endpoints = [ClientEndpoint.model_validate_json(row["body"]) for row in rows]
            identifiers = {id(endpoint): row["endpoint_id"] for endpoint, row in zip(endpoints, rows, strict=True)}
            for original, endpoint in presentation_pairs(eligible_endpoints(user, endpoints, profiles)):
                reasons = access_reasons(user.profiles, profiles, endpoint.inbound_ref)
                if reasons:
                    connections.append(
                        GrantedConnection(
                            id=identifiers[id(original)],
                            remark=endpoint.remark,
                            protocol=endpoint.protocol,
                            inbound_ref=endpoint.inbound_ref,
                            granted_by=reasons,
                            available=endpoint.enabled and user.status == "active",
                        )
                    )
            return PersonAccess(username=username, profiles=user.profiles, inbounds=inbounds, connections=connections)

    def catalog(self) -> AccessProfileCatalog:
        with self.store.lock:
            profiles = read_access_profiles(self.store)
            members = {
                row[0]: row[1]
                for row in self.store.rows("SELECT profile, COUNT(*) FROM user_access_memberships GROUP BY profile")
            }
            all_refs = sorted({inbound.inbound_ref for inbound in self.inbounds() if inbound.inbound_ref})
            entries = [
                AccessProfileSummary(
                    id=DEFAULT_ACCESS_PROFILE,
                    inbound_refs=all_refs,
                    members=members.get(DEFAULT_ACCESS_PROFILE, 0),
                    built_in=True,
                )
            ]
            entries += [
                AccessProfileSummary(id=name, inbound_refs=refs, members=members.get(name, 0))
                for name, refs in profiles.items()
            ]
            return AccessProfileCatalog(
                profiles=entries,
                access_hash=profile_access_hash(self.store),
                declarations_hash=read_profile_declarations_hash(self.store),
                has_non_default_memberships=has_non_default_memberships(self.store),
            )

    def loss_scope(self, lost: Sequence[str]) -> CutOffPlan:
        """Only nodes losing grants; a VLESS cut-off still reconnects every Xray inbound and bridge there."""
        nodes = []
        for row in self.store.rows("SELECT node_id, spec FROM nodes WHERE spec IS NOT NULL ORDER BY node_id"):
            matches = [
                inbound
                for inbound in NodeSpec.model_validate_json(row["spec"]).managed_inbounds
                if inbound.inbound_ref in lost
            ]
            if matches:
                nodes.append(
                    CutOffNode(
                        node_id=row["node_id"],
                        inbound_tags=sorted(inbound.tag for inbound in matches),
                        effect="core_restart"
                        if any(inbound.protocol == "vless" for inbound in matches)
                        else "inbound_reset",
                    )
                )
        return CutOffPlan(nodes=nodes)

    def changed_membership(self, before: UserRecord, after: UserRecord) -> AccessChange:
        profiles = read_access_profiles(self.store)
        refs = {inbound.inbound_ref for inbound in self.inbounds()}
        old = {ref for ref in refs if may_use_inbound(before.profiles, profiles, ref)}
        new = {ref for ref in refs if may_use_inbound(after.profiles, profiles, ref)}
        return AccessChange(
            username=before.username, profiles=after.profiles, gained=sorted(new - old), lost=sorted(old - new)
        )

    def preview(self, request: AccessProfileReviewRequest) -> AccessProfileReview:
        with self.store.lock:
            refuse_used_profiles(self.store, request.access_profiles)
            before_profiles = read_access_profiles(self.store)
            context = {
                (inbound.node_id, inbound.inbound_tag): inbound.inbound_ref for inbound in request.existing_inbounds
            }
            old_refs = {
                ref
                for inbound in self.inbounds()
                if (ref := inbound.inbound_ref or context.get((inbound.node_id, inbound.inbound_tag), ""))
            }
            new_refs = set(request.inbound_refs) if request.inbound_refs else old_refs
            changes = []
            for person in self.read_users():
                before = {ref for ref in old_refs if may_use_inbound(person.profiles, before_profiles, ref)}
                after = {ref for ref in new_refs if may_use_inbound(person.profiles, request.access_profiles, ref)}
                if before != after:
                    changes.append(
                        AccessChange(
                            username=person.username,
                            profiles=person.profiles,
                            gained=sorted(after - before),
                            lost=sorted(before - after),
                        )
                    )
            return AccessProfileReview(
                access_hash=profile_access_hash(self.store),
                access_profiles=request.access_profiles,
                people=changes,
                gained_people=sum(bool(person.gained) for person in changes),
                lost_people=sum(bool(person.lost) for person in changes),
                cut_off=self.loss_scope([ref for person in changes for ref in person.lost]),
            )
