"""Membership filters share paging, contextual counts, and private query permissions."""

import json
from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.console import set_json_mode
from meridian.control.client import ControlClient
from meridian.control.errors import ServiceError
from meridian.control.models import ServiceSettings, UserCreate, UserUpdate
from meridian.core.people_query import PeopleQuery
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.text import unstyle
from tests.support.topology import single_exit_intent
from tests.test_client_bulk_commands import isolated_cli_state as isolated_cli_state
from tests.test_control_people_bulk import commit, preview
from tests.test_control_people_query import people_service as people_service
from tests.test_control_people_query import query, seed


def seed_profiles(service):
    seed(service)
    service.put_settings(
        ServiceSettings(profile_title="Meridian", access_profiles={"limited": [], "other": [], "token": []})
    )
    service.update_user("p-a", UserUpdate(profiles=["limited"], note="Private search sample"))
    service.update_user("p-c", UserUpdate(profiles=["limited", "other"]))


@pytest.mark.parametrize("profile", ["UPPER", "has space", "a" * 49, "inbound:main:reality"])
def test_profile_filter_validates_a_profile_identifier(profile):
    with pytest.raises(ValueError):
        PeopleQuery(profile=profile)


@pytest.mark.parametrize(
    ("profile", "expected"),
    [("limited", ["p-a", "p-c"]), ("other", ["p-c"]), ("default", ["p-d", "p-b"]), ("absent", [])],
)
def test_profile_filter_uses_membership_without_duplicating_people(people_service, profile, expected):
    seed_profiles(people_service)
    before = people_service._db.total_changes
    page = query(people_service, profile=profile)
    assert [person.username for person in page.users] == expected
    assert page.total == len(expected) and page.population == 4
    assert people_service._db.total_changes == before
    assert all(not hasattr(person, "note") and not hasattr(person, "uuid") for person in page.users)
    assert query(people_service, profile="limited", q="Private search sample").total == 0


def test_profile_facets_apply_other_filters_and_count_people_once(people_service):
    seed_profiles(people_service)
    page = query(people_service, profile="limited", status="active")
    assert page.total == 2
    assert page.facets.profile == {"": 3, "default": 1, "limited": 2, "other": 1, "token": 0}
    assert page.facets.status["disabled"] == 0
    assert page.facets.tag["GROUP_A"] == 2 and page.facets.tag["GROUP_B"] == 0
    assert query(people_service, profile="default", q="sample").facets.profile[""] == 1


@pytest.mark.parametrize("sort", ["name", "usage", "expiry", "activity"])
def test_profile_cursor_retains_the_filter_and_excludes_later_people(people_service, sort):
    seed_profiles(people_service)
    first = query(people_service, profile="limited", sort=sort, limit=1)
    people_service.create_user(UserCreate(username="p-added", profiles=["limited"]))
    second = query(people_service, profile="limited", sort=sort, limit=1, cursor=first.next_cursor)
    assert {person.username for person in [*first.users, *second.users]} == {"p-a", "p-c"}
    assert second.next_cursor is None
    with pytest.raises(ServiceError, match="cursor"):
        query(people_service, profile="default", sort=sort, cursor=first.next_cursor)


def test_all_matching_profile_selection_freezes_membership_versions(people_service):
    seed_profiles(people_service)
    review = preview(people_service, filters={"profile": "limited", "limit": 1})
    assert [person.username for person in review.people] == ["p-a", "p-c"]
    people_service.update_user("p-c", UserUpdate(profiles=["default"]))
    with pytest.raises(ServiceError, match="p-c"):
        commit(people_service, review)
    assert people_service.get_user("p-a").status == "active"


@pytest.mark.parametrize("scope", ["read", "propose", "approve", "people", "admin", "explicit", "private"])
def test_profile_query_and_counts_keep_existing_scope_rules(tmp_path, scope):
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        admin.put_settings(ServiceSettings(profile_title="Meridian", access_profiles={"limited": []}))
        admin.create_user(UserCreate(username="sample", profiles=["limited"]))
        if scope in ("explicit", "private"):
            token = admin.create_token(
                "Sample reader", capabilities=["people.query"] + (["people.private"] if scope == "private" else [])
            )
        else:
            token = admin.create_token("Sample reader", scope)
        response = httpx.get(
            url + "/api/people", params={"profile": "limited"}, headers={"Authorization": f"Bearer {token.token}"}
        )
        allowed = scope in ("people", "admin", "private")
        assert response.status_code == (200 if allowed else 403), response.text
        if allowed:
            assert response.json()["facets"]["profile"] == {"": 1, "limited": 1, "default": 0}
            assert response.json()["users"][0]["profiles"] == ["limited"]
        else:
            assert "limited" not in response.text


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_list_and_bulk_cli_share_profile_filters_and_preserve_profile_counts(tmp_path, tmp_home, locale):
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        admin.put_settings(ServiceSettings(profile_title="Meridian", access_profiles={"limited": [], "token": []}))
        admin.create_user(UserCreate(username="sample-a", profiles=["limited"]))
        admin.create_user(UserCreate(username="sample-b"))
        cluster = ClusterConfig(
            panel=PanelConfig(url=url, api_token=ADMIN_TOKEN, server_ip="198.51.100.1"),
            topology_intent=single_exit_intent(users=["topology-person"]),
        )
        try:
            with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
                listed = CliRunner().invoke(app, ["--lang", locale, "client", "list", "--profile", "limited", "--json"])
                assert listed.exit_code == 0, unstyle(listed.output)
                data = json.loads(listed.stdout)["data"]
                assert [person["username"] for person in data["clients"]] == ["sample-a"]
                assert data["clients"][0]["profiles"] == ["limited"]
                assert data["facets"]["profile"]["token"] == 0
                reviewed = CliRunner().invoke(
                    app,
                    [
                        "--lang",
                        locale,
                        "client",
                        "bulk",
                        "disable",
                        "--match-profile",
                        "limited",
                        "--dry-run",
                        "--json",
                    ],
                )
                assert reviewed.exit_code == 0, unstyle(reviewed.output)
                assert [person["username"] for person in json.loads(reviewed.stdout)["data"]["preview"]["people"]] == [
                    "sample-a"
                ]
        finally:
            set_json_mode(False)


def test_invalid_profile_and_explicit_selection_conflicts_stop_before_service_access():
    try:
        with patch("meridian.commands._helpers.ClusterConfig.load") as load:
            for args in (
                ["client", "list", "--profile", "Invalid profile", "--json"],
                ["client", "bulk", "disable", "--person", "sample", "--match-profile", "limited", "--json"],
            ):
                result = CliRunner().invoke(app, args)
                assert result.exit_code == 2
            load.assert_not_called()
    finally:
        set_json_mode(False)
