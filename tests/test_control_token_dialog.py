"""An in-flight token write cannot lose its one-time secret through dialog dismissal."""

from __future__ import annotations

import json

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.test_control_ui_console import run_console


def test_token_issuance_blocks_escape_close_and_duplicate_submits() -> None:
    result = run_console("token-pending")
    assert result["openDuringWrite"] is True and result["closeDisabled"] is True
    assert result["writes"] == 1
    assert result["secret"] == result["issuedSecret"] and result["resultHasForm"] is False
    assert result["stored"] == [] and result["secretInUrl"] is False


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_an_uncertain_issue_reloads_metadata_and_explains_before_retry(locale: str) -> None:
    result = run_console("token-uncertain", locale)
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert result["listReads"] == 2 and result["writes"] == 1
    assert catalog["tokens.uncertain_text"] in result["dialog"]
    assert result["secret"] == ""
