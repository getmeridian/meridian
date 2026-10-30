"""Visible view polling, stale recovery, superseded replies, and request deadlines."""

from __future__ import annotations

import pytest

from tests.test_control_ui_console import run_console


@pytest.mark.parametrize("screen", ["servers", "people"])
def test_live_views_poll_only_while_visible_and_refresh_on_return(screen: str) -> None:
    result = run_console("live-cycle", data={"screen": screen})
    assert result["afterPoll"] == 2 and result["whileHidden"] == 2 and result["afterReturn"] == 3
    assert result["passive"] == [False, True, True]
    assert result["updated"] and result["cleanup"] is True
    assert result["afterStop"] == 3


@pytest.mark.parametrize("screen", ["servers", "people"])
def test_poll_failure_keeps_stale_data_and_inline_retry_recovers(screen: str) -> None:
    result = run_console("live-failure", data={"screen": screen})
    assert result["afterFailure"] == result["beforeFailure"]
    assert result["stale"] is True and result["retryVisible"] is True
    assert result["staleLabel"]
    assert ("10" if screen == "servers" else "30") in result["updated"]
    assert result["recovered"] is True and result["retriedActively"] is True


def test_poll_errors_back_off_and_cap_the_delay() -> None:
    result = run_console("live-backoff", data={"screen": "servers"})
    assert result["gaps"][:5] == [10_000, 20_000, 40_000, 80_000, 120_000]
    assert result["gaps"][-2:] == [120_000, 120_000]


@pytest.mark.parametrize("screen", ["servers", "people"])
def test_a_superseded_poll_cannot_overwrite_newer_state_or_mark_it_stale(screen: str) -> None:
    result = run_console("live-race", data={"screen": screen})
    assert result["afterOld"] == result["afterNew"]
    assert result["stale"] is False and result["fleetTitle"] == "New fleet"


@pytest.mark.parametrize("mode", ["deadline-fetch", "deadline-body"])
def test_request_deadlines_cover_fetch_and_response_body(mode: str) -> None:
    result = run_console(mode)
    assert result["code"] == "timeout" and result["aborted"] is True
    assert result["uncertain"] is True
