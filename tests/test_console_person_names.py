"""Display names in the console: any script, edited in place, refused when taken."""

from __future__ import annotations

import pytest

from tests.support.console_dom import needs_console_dom, run_console

NAMED = {"user": {"display_name": "Sample person"}}

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "fa"])
def test_a_saved_name_retitles_the_person_at_once(locale: str) -> None:
    result = run_console("person-name", locale, {**NAMED, "name": "  Алиса Петрова  "})

    patch = {"method": "PATCH", "url": "api/users/sample", "body": {"display_name": "Алиса Петрова"}}
    assert result["writes"] == [patch]
    assert result["title"] == result["read"] == "Алиса Петрова"
    assert result["usernameShown"] and not result["formOpen"]


def test_clearing_the_name_shows_the_username_again() -> None:
    result = run_console("person-name", data={**NAMED, "name": ""})

    assert result["writes"][0]["body"] == {"display_name": ""}
    assert result["title"] == "sample" and not result["usernameShown"]


def test_a_taken_name_keeps_the_draft_and_says_why() -> None:
    result = run_console("person-name", data={**NAMED, "name": "Bob", "refuse": "name_conflict"})

    assert "Each person needs a different name" in result["error"] and result["formOpen"]
    assert result["title"] == "Sample person"


@pytest.mark.parametrize("name", ["Smith, Alice", "x" * 41], ids=["comma", "too-long"])
def test_names_the_service_would_refuse_never_leave_the_browser(name: str) -> None:
    result = run_console("person-name", data={"name": name})

    assert result["writes"] == [] and result["invalid"] == "true" and result["formOpen"]


@pytest.mark.parametrize(
    ("typed", "body", "assigned"),
    [
        ("alice", {"username": "alice"}, ""),
        ("Алиса", {"display_name": "Алиса"}, "alisa"),
        ("Alice Smith", {"display_name": "Alice Smith"}, "alice-smith"),
    ],
    ids=["latin-username", "any-script", "latin-with-space"],
)
def test_adding_a_person_sends_a_username_only_when_the_name_is_one(
    typed: str, body: dict[str, str], assigned: str
) -> None:
    result = run_console("person-add", data={"name": typed, "assigned": assigned})

    assert [write["url"] for write in result["writes"]] == ["api/users"]
    sent = result["writes"][0]["body"]
    assert {key: sent[key] for key in ("username", "display_name") if key in sent} == body
    username = body.get("username") or assigned
    assert result["hash"] == f"#/people/{username}"
    assert any(typed in toast for toast in result["toasts"])


def test_adding_a_person_with_a_comma_is_refused_before_any_request() -> None:
    result = run_console("person-add", data={"name": "Smith, Alice"})

    assert result["writes"] == [] and result["error"]
