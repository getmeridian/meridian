"""Membership and effective grants stay behind people/admin scopes; policy changes stay reviewed."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from meridian.control.client import ControlClient
from meridian.control.models import ServiceSettings, UserCreate
from tests.support.control_service import ADMIN_TOKEN, serve_control

REF = "inbound:main:reality"


@pytest.mark.parametrize("scope", ["read", "propose", "approve", "people", "admin"])
def test_membership_reads_and_edits_have_people_scope_and_topology_previews_have_admin_scope(
    tmp_path: Path, scope: str
):
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        admin.put_settings(ServiceSettings(profile_title="Meridian", access_profiles={"limited": [REF]}))
        admin.create_user(UserCreate(username="person", profiles=["limited"]))
        token = admin.create_token("Test token", scope).token
        headers = {"Authorization": f"Bearer {token}"}
        person = httpx.get(url + "/api/users/person", headers=headers)
        assert person.status_code == 200
        assert person.json()["profiles"] == (["limited"] if scope in ("people", "admin") else [])
        for path in ("/api/users/person/access", "/api/access-profiles"):
            reply = httpx.get(url + path, headers=headers)
            assert reply.status_code == (200 if scope in ("people", "admin") else 403)
            if reply.status_code != 200:
                assert "limited" not in reply.text
        preview = httpx.post(
            url + "/api/access-profiles/preview", headers=headers, json={"access_profiles": {"limited": []}}
        )
        assert preview.status_code == (200 if scope == "admin" else 403)
        format_preview = httpx.post(url + "/api/subscription/preview", headers=headers, json={"username": "person"})
        assert format_preview.status_code == (200 if scope in ("people", "admin") else 403)
        update = httpx.patch(url + "/api/users/person", headers=headers, json={"profiles": ["default"]})
        assert update.status_code == (200 if scope in ("people", "admin") else 403)


def test_unknown_membership_is_a_plain_atomic_api_error(tmp_path: Path):
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        before = admin.put_user("person")
        reply = httpx.patch(
            url + "/api/users/person",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
            json={"profiles": ["retired"]},
        )
        assert reply.status_code == 400
        assert reply.json()["error"] == {"code": "unknown_profile", "message": "Unknown access profiles: retired."}
        assert admin.get_user("person").profiles == before.profiles


@pytest.mark.parametrize("private", [False, True])
def test_explicit_grants_keep_membership_private_in_reads_and_mutation_results(tmp_path: Path, private: bool):
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        admin.put_settings(ServiceSettings(profile_title="Meridian", access_profiles={"limited": [REF]}))
        admin.create_user(UserCreate(username="person", profiles=["limited"]))
        grants = ["people.read", "people.edit", "people.credentials"] + (["people.private"] if private else [])
        token = admin.create_token("Test token", capabilities=grants).token
        headers = {"Authorization": f"Bearer {token}"}
        reply = httpx.get(url + "/api/users/person", headers=headers)
        assert reply.status_code == 200
        assert reply.json()["profiles"] == (["limited"] if private else [])
        for path in ("/api/users/person/access", "/api/access-profiles"):
            assert httpx.get(url + path, headers=headers).status_code == (200 if private else 403)
        reply = httpx.post(url + "/api/subscription/preview", headers=headers, json={"username": "person"})
        assert reply.status_code == (200 if private else 403)
        reply = httpx.patch(url + "/api/users/person", headers=headers, json={"profiles": ["default"]})
        assert reply.status_code == 200
        assert reply.json()["profiles"] == (["default"] if private else [])
        assert admin.get_user("person").profiles == ["default"]


@pytest.mark.parametrize("private", [False, True])
def test_profile_policy_review_needs_private_access_beside_apply_permission(tmp_path: Path, private: bool):
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        token = admin.create_token(
            "Reviewer", capabilities=["fleet.apply"] + (["people.private"] if private else [])
        ).token
        reply = httpx.post(
            url + "/api/access-profiles/preview",
            headers={"Authorization": f"Bearer {token}"},
            json={"access_profiles": {}},
        )
        assert reply.status_code == (200 if private else 403)


def test_lower_tokens_cannot_read_initial_membership_from_topology_revisions(tmp_path: Path):
    from meridian.control.models import RevisionProposal

    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        revision = admin.propose_revision(
            RevisionProposal(
                intent={"access": {"users": ["person"], "creation_settings": {"person": {"profiles": ["restricted"]}}}},
                plan_hash="a" * 64,
                review_hash="b" * 64,
            )
        )
        for scope in ("read", "propose", "approve", "people", "admin"):
            token = admin.create_token("Test token", scope).token
            reply = httpx.get(url + f"/api/revisions/{revision.number}", headers={"Authorization": f"Bearer {token}"})
            assert reply.status_code == (403 if scope == "people" else 200)
            if scope == "people":
                continue
            settings = reply.json()["intent"]["access"]["creation_settings"]["person"]
            assert ("profiles" in settings) == (scope in ("people", "admin"))


@pytest.mark.parametrize("private", [False, True])
def test_explicit_revision_grants_cover_private_creation_membership_in_proposals_and_details(
    tmp_path: Path, private: bool
):
    from meridian.control.models import RevisionProposal

    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        proposal = RevisionProposal(
            intent={"access": {"users": ["person"], "creation_settings": {"person": {"profiles": ["restricted"]}}}},
            plan_hash="a" * 64,
            review_hash="b" * 64,
        )
        token = admin.create_token(
            "Reviewer", capabilities=["revisions.read", "revisions.propose"] + (["people.private"] if private else [])
        ).token
        with ControlClient(url, token, report_mutations=False) as reviewer:
            revision = reviewer.propose_revision(proposal)
            for record in (revision, reviewer.revision(revision.number)):
                assert record is not None
                settings = record.intent["access"]["creation_settings"]["person"]
                assert ("profiles" in settings) == private
