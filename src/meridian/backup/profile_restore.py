"""Profile compatibility is reviewed before restore stages or touches its target."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import NoReturn

from meridian.backup import BackupError
from meridian.cluster import ClusterConfig
from meridian.compiler import compile_topology
from meridian.compiler.models import ResourcePlan, ServiceSettingsPayload
from meridian.control.client import ControlClient
from meridian.control.restore import RestoreError, read_deployer_state, read_snapshot_profile_access
from meridian.core.access_profile_views import AccessProfileReview
from meridian.core.access_profiles import profile_declarations_hash
from meridian.core.agent_types import Sha256Hex
from meridian.core.errors import LocalStateError
from meridian.core.models import CoreModel
from meridian.i18n import t
from meridian.setup.profile_review import review_profile_access


class RestoreProfileReview(CoreModel):
    declarations_hash: Sha256Hex
    profiles_used: bool


def load_deposited_cluster(cluster_yml: str, servers_json: str, fleet_id: str) -> ClusterConfig:
    """Validate deposited files without writing them into the current context."""
    with tempfile.TemporaryDirectory() as scratch:
        staged = Path(scratch) / "cluster.yml"
        staged.write_text(cluster_yml, encoding="utf-8")
        try:
            deposited = ClusterConfig.load(staged)
            json.loads(servers_json)
        except (LocalStateError, ValueError) as exc:
            raise BackupError(f"The snapshot's fleet files do not load: {exc}", category="bug") from exc
    if deposited.fleet_id != fleet_id:
        raise BackupError("The snapshot's fleet files name another fleet.", category="bug")
    return deposited


def refuse_profile_restore_mismatch() -> NoReturn:
    raise BackupError(t("profiles.restore_mismatch"), hint=t("profiles.restore_mismatch_hint"), category="user")


def review_restore_profiles(cluster: ClusterConfig, database: bytes | None) -> RestoreProfileReview:
    """Known declarations may expand; a snapshot without their fingerprint needs an identical map."""
    intent = cluster.topology_intent
    if intent is None or not cluster.runs_control_service:
        raise BackupError(
            "Only a fleet on Meridian's control service can be restored from a snapshot.", category="user"
        )
    plan = compile_topology(intent)
    declarations = profile_declarations_hash(intent.access_profiles)
    restore = cluster.control_restore
    if restore is not None and restore.step != "done":
        if restore.profile_declarations_hash and restore.profile_declarations_hash != declarations:
            refuse_profile_restore_mismatch()
        if restore.reached("staged") and restore.staged_database:
            try:
                database = Path(restore.staged_database).read_bytes()
            except OSError as exc:
                raise BackupError(
                    t("profiles.restore_evidence_missing"), hint=t("profiles.restore_mismatch_hint"), category="user"
                ) from exc
        elif database is None and restore.profile_declarations_hash:
            return RestoreProfileReview(declarations_hash=declarations, profiles_used=cluster.has_used_access_profiles)
    if database is None:
        raise BackupError(
            t("profiles.restore_evidence_missing"), hint=t("profiles.restore_mismatch_hint"), category="user"
        )
    try:
        state = read_snapshot_profile_access(database)
    except RestoreError as exc:
        raise BackupError(
            t("profiles.restore_state_unreadable"), hint=t("profiles.restore_state_retry"), category="user"
        ) from exc
    if state.declarations_hash is not None:
        if state.declarations_hash != declarations:
            refuse_profile_restore_mismatch()
    else:
        wanted = next(
            row.payload.access_profiles for row in plan.resources if isinstance(row.payload, ServiceSettingsPayload)
        )
        if state.access_profiles != wanted or (state.has_non_default_memberships and not state.access_profiles):
            refuse_profile_restore_mismatch()
    used = bool(state.declarations_hash or state.access_profiles or state.has_non_default_memberships)
    deposit = read_deployer_state(database)
    if deposit is not None:
        deposited = load_deposited_cluster(deposit.cluster_yml, deposit.servers_json, cluster.fleet_id)
        used = used or deposited.has_used_access_profiles
    return RestoreProfileReview(declarations_hash=declarations, profiles_used=used or cluster.has_used_access_profiles)


def review_restored_profile_access(cluster: ClusterConfig, plan: ResourcePlan) -> AccessProfileReview | None:
    """Adopt matching legacy declarations; fingerprinted restores use the normal unchanged-write fence."""
    intent = cluster.topology_intent
    assert intent is not None
    with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
        live = client.access_profiles()
    if live.declarations_hash is not None:
        if live.declarations_hash != profile_declarations_hash(intent.access_profiles):
            refuse_profile_restore_mismatch()
        return None
    profiles = {profile.id: profile.inbound_refs for profile in live.profiles if not profile.built_in}
    wanted = next(
        row.payload.access_profiles for row in plan.resources if isinstance(row.payload, ServiceSettingsPayload)
    )
    if profiles != wanted or (live.has_non_default_memberships and not profiles):
        refuse_profile_restore_mismatch()
    if not profiles:
        return None
    review = review_profile_access(cluster, plan)
    if review is None or review.access_hash != live.access_hash:
        refuse_profile_restore_mismatch()
    return review
