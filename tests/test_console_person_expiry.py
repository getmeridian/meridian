"""Precise browser-local expiry edits expose their exact UTC instant and change only expiry."""

import pytest

from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_person_expiry_edits_native_date_and_time_with_zone_and_exact_utc(
    locale: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    result = run_console("person-expiry", locale, {"date": "2032-01-15", "time": "12:34:56"})
    assert result["hasTime"] is True
    assert "Pacific/Honolulu" in result["zone"]
    assert result["utc"] == "2032-01-15T22:34:56Z"
    assert result["writes"] == [{"expire_at": "2032-01-15T22:34:56Z"}]


@pytest.mark.parametrize("months", [1, 3])
def test_month_presets_fill_the_draft_from_a_core_preview_before_saving(months: int) -> None:
    expiry = "2032-02-29T10:20:30Z" if months == 1 else "2032-04-30T10:20:30Z"
    result = run_console("person-expiry", data={"months": months, "preview": expiry})
    assert result["beforeSave"] == []
    assert result["previewReads"] == ["ui/api/people/sample/expiry?months=" + str(months)]
    assert result["utc"] == expiry
    assert result["writes"] == [{"expire_at": expiry}]


def test_no_expiry_remains_explicit_null_and_untouched_fractional_expiry_is_preserved() -> None:
    cleared = run_console("person-expiry", data={"clear": True})
    assert cleared["writes"] == [{"expire_at": None}]
    unchanged = run_console(
        "person-expiry", data={"strategy": "week", "user": {"expire_at": "2032-01-31T10:20:30.500Z"}}
    )
    assert unchanged["writes"] == [{"traffic_limit_strategy": "week"}]


@pytest.mark.parametrize(("date", "time"), [("2032-01-15", ""), ("2020-01-01", "12:00:00"), ("2099-01-01", "12:00:00")])
def test_invalid_or_past_new_expiry_is_refused_before_a_patch(date: str, time: str) -> None:
    result = run_console("person-expiry", data={"date": date, "time": time})
    assert result["writes"] == [] and result["error"]
