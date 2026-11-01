"""The person screen displays the service's counters and its historical-split disclosure."""

import json

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_person_traffic_uses_the_api_period_counters_and_discloses_unattributed_use(locale: str) -> None:
    result = run_console(
        "person-period-traffic",
        locale,
        {
            "user": {
                "used_traffic_bytes": 43,
                "lifetime_traffic_bytes": 113,
                "period_traffic": {"upload_bytes": 4, "download_bytes": 39, "unattributed_bytes": 30},
            }
        },
    )
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())

    def plain(value: str) -> str:
        return value.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789", "\u2068\u2069"))

    assert [plain(value) for value in result["facts"][:4]] == [
        catalog["usage.up"],
        "4 B",
        catalog["usage.down"],
        "39 B",
    ]
    assert plain(result["note"]) == catalog["person.traffic_unattributed"].replace("{bytes}", "30 B")
