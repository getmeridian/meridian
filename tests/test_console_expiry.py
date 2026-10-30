"""Console expiry edits stay outside the legacy no-expiry sentinel range."""

import json

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("screen", ["add", "edit"])
@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_console_expiry_refuses_the_legacy_never_range_at_the_field(screen: str, locale: str) -> None:
    result = run_console("expiry-" + screen, locale, {"date": "2099-01-01"})
    assert result["writes"] == []
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert result["error"] == catalog["people.expiry_too_far"] and result["invalid"] is True


@pytest.mark.parametrize("screen", ["add", "edit"])
def test_console_checks_the_utc_expiry_after_local_end_of_day_conversion(
    screen: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TZ", "Pacific/Honolulu")
    result = run_console("expiry-" + screen, data={"date": "2098-12-31"})
    assert result["writes"] == [] and result["error"] and result["invalid"] is True


@pytest.mark.parametrize("screen", ["add", "edit"])
@pytest.mark.parametrize("date", ["2088-12-31", ""])
def test_console_expiry_keeps_valid_dates_and_explicit_removal(screen: str, date: str) -> None:
    result = run_console("expiry-" + screen, data={"date": date})
    assert result["error"] == "" and len(result["writes"]) == 1
    expiry = result["writes"][0]["expire_at"]
    assert expiry is None if not date else expiry.startswith(date)
