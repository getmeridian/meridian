"""Which panel users `meridian migrate` imports, with what, and what it reports as left behind."""

from __future__ import annotations

import pytest

from meridian.migrate import MigrationError
from meridian.migrate.users import carry_users
from meridian.remnawave import User
from meridian.remnawave.client import ACCESS_USER_DESCRIPTION, ACCESS_USER_TAG

ALICE_UUID = "3f2a9c4e-7b1d-4e6a-9c2f-1a2b3c4d5e6f"

REMNAWAVE_NEVER = "2099-12-31T23:59:59.000Z"


def _user(username: str = "alice", **changes: object) -> User:
    fields: dict[str, object] = {
        "id": "7",
        "username": username,
        "short_uuid": f"{username}ShortUuid0001"[:16].ljust(16, "0"),
        "vless_uuid": ALICE_UUID if username == "alice" else f"{ALICE_UUID[:-2]}{len(username):02d}",
        "status": "ACTIVE",
        "expire_at": REMNAWAVE_NEVER,
        "traffic_limit_strategy": "NO_RESET",
        "description": ACCESS_USER_DESCRIPTION,
        "created_at": "2026-01-31T12:00:00.000Z",
        **changes,
    }
    return User(**fields)  # type: ignore[arg-type]


def test_an_intent_user_carries_credentials_status_expiry_limits_and_usage() -> None:
    panel = _user(
        status="LIMITED",
        expire_at="2027-03-01T00:00:00.000Z",
        traffic_limit_bytes=50 * 10**9,
        traffic_limit_strategy="WEEK",
        used_traffic_bytes=51 * 10**9,
        lifetime_used_traffic_bytes=300 * 10**9,
        last_traffic_reset_at="2026-10-26T00:00:00.000Z",
        created_at="2026-01-02T03:04:05.678Z",
    )

    carried = carry_users([panel], ["alice"])

    (alice,) = carried.batch.users
    assert (alice.uuid, alice.sub_token, alice.status) == (ALICE_UUID, panel.short_uuid, "limited")
    assert (alice.expire_at, alice.traffic_limit_strategy) == ("2027-03-01T00:00:00Z", "week")
    assert (alice.used_traffic_bytes, alice.lifetime_traffic_bytes) == (51 * 10**9, 300 * 10**9)
    assert (alice.traffic_reset_at, alice.created_at) == ("2026-10-26T00:00:00Z", "2026-01-02T03:04:05Z")
    assert carried.notes == {}


def test_remnawaves_never_is_no_expiry() -> None:
    assert carry_users([_user()], ["alice"]).batch.users[0].expire_at is None


def test_description_and_valid_tag_become_private_metadata() -> None:
    description = "Temporary invitation\nKeep the original text"
    carried = carry_users([_user(description=description, tag="TRIAL_7")], ["alice"])
    person = carried.batch.users[0]
    assert (person.note, person.tag) == (description, "TRIAL_7")
    assert carried.notes == {}


def test_long_description_is_truncated_and_reported_without_its_contents() -> None:
    description = "Private text " * 50
    carried = carry_users([_user(description=description)], ["alice"])
    assert carried.batch.users[0].note == description[:500]
    assert any("truncated to 500 characters" in note for note in carried.notes["alice"])
    assert description not in str(carried.notes)


def test_invalid_tag_is_left_behind_and_rejected_person_contacts_stay_out() -> None:
    carried = carry_users([_user(tag="bad-tag", email="sample@example.com", telegram_id="1000001")], ["alice"])
    person = carried.batch.users[0]
    assert person.tag == ""
    assert {"invalid tag", "email", "Telegram ID"}.issubset(set(carried.notes["alice"]))
    assert "email" not in person.model_dump() and "telegram_id" not in person.model_dump()


def test_only_intent_users_move_and_the_rest_is_named() -> None:
    carried = carry_users([_user(), _user("meridian_bridge_a"), _user("handmade")], ["alice"])

    assert [user.username for user in carried.batch.users] == ["alice"]
    assert carried.skipped == ["handmade", "meridian_bridge_a"]


@pytest.mark.parametrize(
    ("changes", "note"),
    [
        ({"telegram_id": "1000001"}, "Telegram ID"),
    ],
    ids=["telegram"],
)
def test_what_the_service_cannot_hold_is_reported(changes: dict[str, object], note: str) -> None:
    carried = carry_users([_user(**changes)], ["alice"])

    assert any(item.startswith(note) for item in carried.notes["alice"])


def test_a_rolling_month_imports_with_its_original_anchor() -> None:
    carried = carry_users([_user(traffic_limit_strategy="MONTH_ROLLING")], ["alice"])

    assert carried.batch.users[0].traffic_limit_strategy == "month_rolling"
    assert carried.batch.users[0].traffic_reset_anchor_day == 31
    assert carried.notes == {}


def test_meridians_own_description_and_tag_are_not_reported() -> None:
    assert carry_users([_user(tag=ACCESS_USER_TAG)], ["alice"]).notes == {}


def test_an_intent_user_missing_from_the_panel_is_refused() -> None:
    with pytest.raises(MigrationError, match="no user for: bob") as error:
        carry_users([_user()], ["alice", "bob"])

    assert error.value.category == "user"


def test_a_short_uuid_outside_the_link_format_is_refused_with_a_reissue_hint() -> None:
    with pytest.raises(MigrationError, match="alice") as error:
        carry_users([_user(short_uuid="hand.set")], ["alice"])

    assert "client reissue" in error.value.hint


def test_an_unknown_status_is_refused() -> None:
    with pytest.raises(MigrationError, match="does not know"):
        carry_users([_user(status="ON_HOLD")], ["alice"])
