"""Both sides of a presentation handover with real HTTP, persisted service state, and no reachable exits."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import httpx

from meridian.control.client import ControlClient
from meridian.control.models import ServiceSettings
from meridian.core.presentation import PresentationPatch
from tests.support.control_service import ADMIN_TOKEN, serve_control

AUTH = {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def settings(title: str, hours: int, label: str, provider: str, **servers) -> ServiceSettings:
    return ServiceSettings.model_validate(
        {
            "profile_title": title,
            "update_interval_hours": hours,
            "owner_contact": {"text": label},
            "servers": {"edge": {"node_id": "main", "metadata": {"provider": provider}}, **servers},
        }
    )


def test_upgrade_preserves_committed_b12_settings_and_seeds_missing_server_rows(tmp_path: Path) -> None:
    live = settings("Legacy title", 37, "Support on the live fleet", "Paid provider")
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as old:
        old.put_settings(live)
    # b12 writes these same settings/meta rows, but has no presentation version or ownership marker.
    with sqlite3.connect(tmp_path / "control.db") as db:
        db.execute("DELETE FROM meta WHERE key LIKE 'presentation_%'")
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        initial = admin.presentation()
        assert not initial.seeded and initial.version == 0
        stale = settings(
            "Stale deployer",
            6,
            "Wrong contact",
            "Old invoice",
            unregistered={"node_id": "new", "metadata": {"allowance_bytes": 877}},
        )
        admin.put_settings(stale, adopt_presentation=True)
        adopted = admin.presentation()
        assert (adopted.profile_title, adopted.update_interval_hours, adopted.owner_contact) == (
            initial.profile_title,
            37,
            initial.owner_contact,
        )
        assert adopted.servers["edge"].provider == "Paid provider"
        assert adopted.servers["unregistered"].allowance_bytes == 877
        assert admin.node("new") is None  # No agent has enrolled or reported.
        refused = httpx.put(url + "/api/settings", headers=AUTH, json=stale.to_data())
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "presentation_adopted"
        assert admin.presentation() == adopted


def test_owner_edit_before_first_seed_keeps_only_that_field_and_refuses_old_or_stale_writer(tmp_path: Path) -> None:
    seed = settings("Wizard title", 24, "Wizard support", "Wizard billing")
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        original = admin.presentation()
        admin.patch_presentation(PresentationPatch(expected_version=original.version, profile_title="Phone title"))
        # Resetting to the default is still the owner's choice and must survive restart.
        edited = admin.patch_presentation(
            PresentationPatch(expected_version=original.version + 1, profile_title="Meridian")
        )
        assert not edited.seeded
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        for extra, code in [
            ({}, "presentation_adopted"),
            ({"adopt_presentation": True, "expected_presentation_version": original.version}, "stale_presentation"),
        ]:
            reply = httpx.put(url + "/api/settings", headers=AUTH, json={**seed.to_data(), **extra})
            assert reply.status_code == 409 and reply.json()["error"]["code"] == code
        assert admin.presentation() == edited
        admin.put_settings(seed, adopt_presentation=True)
        read = admin.presentation()
        assert read.seeded and read.profile_title == "Meridian"
        assert (
            read.update_interval_hours == 24
            and read.owner_contact == seed.owner_contact
            and read.servers["edge"] == seed.servers["edge"].metadata
        )
        # Subsequent applies add only membership. A new server's former metadata is never replayed.
        added = settings(
            "Stale seed", 6, "Old", "Stale", later={"node_id": "offline", "metadata": {"provider": "Old bill"}}
        )
        admin.put_settings(added, adopt_presentation=True)
        after = admin.presentation()
        assert after.servers["later"].provider == "" and after.servers["edge"] == read.servers["edge"]
        edit = PresentationPatch.model_validate(
            {"expected_version": after.version, "servers": {"later": {"provider": "Actual bill"}}}
        )
        assert admin.patch_presentation(edit).servers["later"].provider == "Actual bill"
        assert admin.node("offline") is None


def test_empty_server_patch_is_rejected_without_claiming_the_service(tmp_path: Path) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        before = admin.presentation()
        for servers in ({}, {"edge": {}}):
            reply = httpx.patch(
                url + "/api/settings/presentation",
                headers=AUTH,
                json={"expected_version": before.version, "servers": servers},
            )
            assert reply.status_code == 400
        assert admin.presentation() == before
