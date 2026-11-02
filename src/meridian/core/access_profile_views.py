"""Secret-free effective access and reviewed profile-change contracts."""

from __future__ import annotations

from pydantic import Field

from meridian.core.access_profiles import AccessProfileMap, InboundReference, ProfileMembership
from meridian.core.agent_types import Sha256Hex
from meridian.core.client_sessions import CutOffPlan
from meridian.core.inputs import TopologyIdValue
from meridian.core.models import CoreModel
from meridian.core.protocols import ProtocolKind


class GrantedInbound(CoreModel):
    node_id: str
    inbound_tag: str
    inbound_ref: str
    granted_by: list[TopologyIdValue]


class GrantedConnection(CoreModel):
    id: str
    remark: str
    protocol: ProtocolKind
    inbound_ref: str
    granted_by: list[TopologyIdValue]
    available: bool


class PersonAccess(CoreModel):
    username: str
    profiles: ProfileMembership
    inbounds: list[GrantedInbound]
    connections: list[GrantedConnection]


class AccessProfileSummary(CoreModel):
    id: TopologyIdValue
    inbound_refs: list[str]
    members: int = Field(ge=0)
    built_in: bool = False


class AccessProfileCatalog(CoreModel):
    profiles: list[AccessProfileSummary]
    access_hash: Sha256Hex


class AccessProfileReviewRequest(CoreModel):
    access_profiles: AccessProfileMap
    inbound_refs: list[InboundReference] = Field(default_factory=list, max_length=10000)
    existing_inbounds: list[GrantedInbound] = Field(default_factory=list, max_length=10000)


class AccessChange(CoreModel):
    username: str
    profiles: ProfileMembership
    gained: list[str]
    lost: list[str]


class AccessProfileReview(CoreModel):
    access_hash: Sha256Hex
    access_profiles: AccessProfileMap
    people: list[AccessChange]
    gained_people: int = Field(ge=0)
    lost_people: int = Field(ge=0)
    cut_off: CutOffPlan
