"""Declaration fingerprints remain independent of expansion and atomic with service settings."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from meridian.compiler import compile_topology
from meridian.compiler.models import ServiceSettingsPayload
from meridian.control.client import ControlClient, ControlServiceConflict
from meridian.control.errors import ServiceError
from meridian.control.models import ServiceSettings, UserCreate
from meridian.core.access_profile_views import AccessProfileReviewRequest
from meridian.core.access_profiles import AccessProfileIntent, profile_declarations_hash
from meridian.core.topology import SetupIntent
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.topology import single_exit_intent
from tests.test_access_profiles import POLICY, configure_profiles
from tests.test_access_profiles import service as service


def fingerprint(grant: str = "exit:main") -> str:
    return profile_declarations_hash([AccessProfileIntent(id="limited", grants=[grant])])


def test_fingerprint_normalizes_profile_and_grant_order_but_keeps_declarations():
    first = [
        AccessProfileIntent(id="limited", grants=["gateway:gw", "exit:main"]),
        AccessProfileIntent(id="other", grants=[]),
    ]
    reordered = [
        AccessProfileIntent(id="other", grants=[]),
        AccessProfileIntent(id="limited", grants=["exit:main", "gateway:gw"]),
    ]
    assert profile_declarations_hash(first) == profile_declarations_hash(reordered)
    assert fingerprint() != fingerprint("inbound:main:reality")


def test_equivalent_compiled_maps_do_not_make_different_declarations_equivalent():
    before = single_exit_intent(access_profiles=[AccessProfileIntent(id="limited", grants=["exit:exit-a"])])
    after = SetupIntent.model_validate(
        {**before.to_data(), "access_profiles": [{"id": "limited", "grants": ["inbound:exit-a:reality-a"]}]}
    )
    old = next(
        row.payload for row in compile_topology(before).resources if isinstance(row.payload, ServiceSettingsPayload)
    )
    new = next(
        row.payload for row in compile_topology(after).resources if isinstance(row.payload, ServiceSettingsPayload)
    )
    assert old.access_profiles == new.access_profiles
    assert old.access_profile_declarations_hash != new.access_profile_declarations_hash


def test_reviewed_fingerprint_is_atomic_with_settings_and_survives_restart(service, tmp_path: Path):
    configure_profiles(service)
    before = service.settings()
    settings = ServiceSettings.model_validate({**before.to_data(), "access_profile_declarations_hash": fingerprint()})
    review = service.access.preview(AccessProfileReviewRequest(access_profiles=POLICY))
    with (
        patch.object(service, "_compose_all", side_effect=RuntimeError("Composition failed.")),
        pytest.raises(RuntimeError),
    ):
        service.put_settings(settings, expected_access_hash=review.access_hash)
    assert service.settings() == before
    assert service.access.catalog().declarations_hash is None
    service.put_settings(settings, expected_access_hash=review.access_hash)
    assert service.settings().access_profile_declarations_hash == fingerprint()
    assert service.access.catalog().declarations_hash == fingerprint()
    from meridian.control.service import ControlService
    from tests.support.control_service import FLEET_ID

    copy = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert copy.settings().access_profile_declarations_hash == fingerprint()
    finally:
        copy.close()


def test_unreviewed_settings_recheck_declarations_and_change_nothing_on_a_race(tmp_path: Path):
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        original = ServiceSettings(
            profile_title="Meridian", access_profiles={"limited": []}, access_profile_declarations_hash=fingerprint()
        )
        admin.put_settings(original, expected_profile_declarations_hash=fingerprint())
        stale = ServiceSettings.model_validate({**original.to_data(), "profile_title": "Stale title"})
        changed = ServiceSettings.model_validate(
            {**original.to_data(), "access_profile_declarations_hash": fingerprint("gateway:gw")}
        )
        review = admin.preview_access_profiles(AccessProfileReviewRequest(access_profiles=original.access_profiles))
        admin.put_settings(changed, expected_access_hash=review.access_hash)
        with pytest.raises(ControlServiceConflict, match="declarations changed") as refused:
            admin.put_settings(stale, expected_profile_declarations_hash=fingerprint())
        assert "meridian profile" in str(refused.value) and "meridian apply" in str(refused.value)
        assert admin.settings() == changed
        with pytest.raises(ControlServiceConflict, match="changed since review"):
            admin.put_settings(original, expected_access_hash=review.access_hash)
        assert admin.settings() == changed


def test_empty_fingerprint_is_kept_after_the_last_profile_is_removed(service):
    configure_profiles(service)
    reviewed = service.access.preview(AccessProfileReviewRequest(access_profiles={}))
    empty = profile_declarations_hash([])
    service.put_settings(
        ServiceSettings(profile_title="Meridian", access_profile_declarations_hash=empty),
        expected_access_hash=reviewed.access_hash,
    )
    assert service.settings().access_profile_declarations_hash == empty
    service.put_settings(ServiceSettings(profile_title="New title"))
    assert service.settings().access_profile_declarations_hash == empty
    with pytest.raises(ServiceError, match="declarations changed"):
        service.put_settings(
            ServiceSettings(
                profile_title="New title", access_profiles=POLICY, access_profile_declarations_hash=fingerprint()
            ),
            expected_profile_declarations_hash=fingerprint(),
        )
    assert service.settings().access_profiles == {}


def test_settings_cannot_replace_an_established_fingerprint_without_either_fence(service):
    configure_profiles(service)
    review = service.access.preview(AccessProfileReviewRequest(access_profiles=POLICY))
    service.put_settings(
        ServiceSettings(
            profile_title="Meridian", access_profiles=POLICY, access_profile_declarations_hash=fingerprint()
        ),
        expected_access_hash=review.access_hash,
    )
    before = service.settings()
    for changes in ({"access_profiles": {}}, {"access_profile_declarations_hash": fingerprint("gateway:gw")}):
        with pytest.raises(ServiceError, match="need review"):
            service.put_settings(ServiceSettings.model_validate({**before.to_data(), **changes}))
        assert service.settings() == before


def test_membership_without_a_live_map_or_fingerprint_still_prevents_bootstrap(service):
    configure_profiles(service)
    service.create_user(UserCreate(username="person", profiles=["limited"]))
    with service._store.transaction():
        service._store.set_meta("access_profiles", "{}")
    catalog = service.access.catalog()
    assert catalog.declarations_hash is None
    assert catalog.has_non_default_memberships is True
    assert all(profile.built_in for profile in catalog.profiles)
    with pytest.raises(ServiceError, match="declarations changed"):
        service.put_settings(
            ServiceSettings(
                profile_title="Meridian", access_profiles=POLICY, access_profile_declarations_hash=fingerprint()
            ),
            expected_profile_declarations_hash=fingerprint(),
        )
    assert service.get_user("person").profiles == ["limited"]
    assert service.settings().access_profiles == {}
