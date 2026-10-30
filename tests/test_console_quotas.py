"""The console and CLI agree on binary bytes and refuse invalid limits at their boundaries."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from meridian.commands._helpers import format_traffic
from meridian.control.models import UserCreate, UserUpdate
from meridian.core.client_limits import ClientLimitChange, parse_traffic_limit
from tests.support.console_dom import needs_console_dom, run_console


@needs_console_dom
@pytest.mark.parametrize(
    ("amount", "unit"),
    [
        ("1", "GiB"),
        ("1.5", "TiB"),
        ("0.00390625", "GiB"),
        ("0.0000000023283064365386962890625", "GiB"),
    ],
)
def test_console_and_cli_send_the_same_bytes(amount: str, unit: str) -> None:
    result = run_console("quota", data={"amount": amount, "unit": unit, "mode": "limited"})
    assert result["error"] == ""
    assert result["change"]["traffic_limit_bytes"] == parse_traffic_limit(amount + unit)
    assert result["units"] == ["GiB", "TiB"]


@needs_console_dom
@pytest.mark.parametrize("amount", ["-1", "Infinity", "NaN", "", "8589934592", "8388608"])
def test_invalid_console_quota_blocks_form_submission_at_the_field(amount: str) -> None:
    result = run_console("quota", data={"amount": amount, "unit": "GiB", "mode": "limited"})
    assert result["change"] is None and result["error"]
    assert result["invalid"] is True


@needs_console_dom
def test_unlimited_is_an_explicit_choice() -> None:
    result = run_console("quota", data={"amount": "", "unit": "GiB", "mode": "unlimited"})
    assert result["change"]["traffic_limit_bytes"] == 0
    assert result["disabled"] is True and result["error"] == ""


@pytest.mark.parametrize("model", [UserCreate, UserUpdate, ClientLimitChange])
def test_api_quota_models_refuse_a_value_above_sqlite_int64(model) -> None:
    fields = {"traffic_limit_bytes": 2**63}
    if model is UserCreate:
        fields["username"] = "sample"
    with pytest.raises(ValidationError):
        model.model_validate(fields)


def test_cli_traffic_labels_match_binary_console_units() -> None:
    assert format_traffic(1024, 1024**3) == "1 KiB / 1 GiB"
