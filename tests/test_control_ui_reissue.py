"""Browser reissue reviews the same cut-off consent and outcomes as removal."""

import json

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_reissue_reviews_cut_off_and_shows_its_per_node_outcome(locale: str) -> None:
    result = run_console("command-reissue-fallback", locale)
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert result["writesBeforeConfirm"] == 0 and result["defaultOn"] is False
    assert catalog["commands.cut_off_help"] in result["disclosure"]
    assert catalog["person.reissue_confirm"] in result["disclosure"] and "main" in result["disclosure"]
    assert result["writes"] == [{"body": {"cut_off": True}, "csrf": "1"}]
    assert catalog["commands.core_restart"] in result["resultText"]
    assert result["requests"].count("GET:api/users/person-a/cut-off") == 1
    assert result["requests"].count("GET:ui/api/people/person-a/share") == 2


@pytest.mark.parametrize("mode", ["default", "cancel", "declared", "no-nodes"])
def test_reissue_defaults_to_no_cut_off_and_keeps_declared_people_on_the_cli(mode: str) -> None:
    result = run_console("command-reissue-" + mode)
    expected = [{"body": {"cut_off": False}, "csrf": "1"}] if mode in {"default", "no-nodes"} else []
    assert result["writes"] == expected
    if mode == "declared":
        assert "meridian client reissue person-a" in result["declaredText"] and result["hasRemove"] is False
    if mode == "no-nodes":
        assert result["choiceDisabled"] is True
    assert not any("/commands/" in request for request in result["requests"])


@pytest.mark.parametrize("mode", ["failed", "timed_out"])
def test_reissue_keeps_the_new_person_view_when_cut_off_fails_or_times_out(mode: str) -> None:
    result = run_console("command-reissue-" + mode)
    catalog = json.loads((UI_DIR / "locales/en.json").read_text())
    assert catalog["commands." + mode] in result["resultText"]
    assert result["images"] == 0 and result["pwned"] is False
    assert result["requests"].count("GET:ui/api/people/person-a/share") == 2
