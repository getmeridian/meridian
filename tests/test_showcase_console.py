"""Exercise the exported console against its demo adapter, without any service running."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.build_showcase import export_showcase, validate_recordings
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("console-demo")
    export_showcase(directory)
    return directory


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_demo_opens_people_and_navigates_the_real_screens_without_api_requests(demo: Path, locale: str) -> None:
    result = run_console("showcase-navigation", locale, {"directory": str(demo)})
    assert result["lang"] == locale
    assert result["dir"] == ("rtl" if locale == "fa" else "ltr")
    assert result["people"] == 6
    assert result["person"] == "Luma"
    assert result["connections"] == 8
    assert result["qr"]
    assert result["servers"] == 2
    assert result["settings"] and result["tokens"] == 3
    assert result["banner"] == f"/demo/?lang={locale}"
    assert result["outside"] == result["nativeApi"] == result["errors"] == []


def test_demo_mutations_preserve_current_contracts_and_reset_with_a_new_document(demo: Path) -> None:
    result = run_console("showcase-mutations", data={"directory": str(demo)})
    validate_recordings(result["responses"])
    assert result["edited"] == "Literal <b>demo note</b>"
    assert result["devices"] == 0
    assert result["notice"] == "Hello Luma"
    assert result["language"] == "zh"
    assert result["reissued"] and result["token_revoked"] and result["removed"]
    assert result["outside"] == result["nativeApi"] == result["errors"] == []
    reset = run_console("showcase-reset", data={"directory": str(demo)})
    assert reset["note"] != result["edited"]
    assert reset["language"] == "auto" and reset["devices"] == 2
