"""Client limits and expiry: parsing, both backends' wire shapes, and applies leaving them alone."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.control.client import ControlClient
from meridian.control_access import ServiceAccessPanel
from meridian.core.client_limits import (
    ClientLimitChange,
    ClientLimitError,
    build_limit_change,
    canonical_expiry,
    parse_expiry,
    parse_traffic_limit,
    parse_traffic_reset,
)
from meridian.core.clients import build_client_record
from meridian.remnawave.models import NEVER_EXPIRES_AT
from meridian.setup.single_server import single_server_intent
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.remnawave_http import FakeRemnawave

GB = 1024**3
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
FUTURE_DAY = "2031-12-31"


# --- Parsing ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("50GB", 50 * GB),
        ("50gb", 50 * GB),
        ("50 GB", 50 * GB),
        ("50GiB", 50 * GB),
        ("500MB", 500 * 1024**2),
        ("1.5TB", int(1.5 * 1024**4)),
        ("1024KB", 1024**2),
        ("۵۰GB", 50 * GB),
        ("none", 0),
        ("unlimited", 0),
        ("0", 0),
    ],
    ids=["gb", "lowercase", "space", "gib", "mb", "fraction", "kb", "persian-digits", "none", "unlimited", "zero"],
)
def test_traffic_limit_parses_binary_sizes(text: str, expected: int) -> None:
    assert parse_traffic_limit(text) == expected


@pytest.mark.parametrize(
    "text",
    ["50", "50XB", "GB", "-5GB", "fifty GB", "0.0000001KB", ""],
    ids=["no-unit", "bad-unit", "no-number", "negative", "words", "rounds-to-zero", "empty"],
)
def test_traffic_limit_refuses_what_is_not_a_size(text: str) -> None:
    with pytest.raises(ClientLimitError) as exc_info:
        parse_traffic_limit(text)
    assert exc_info.value.category == "user"
    assert exc_info.value.hint


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("day", "day"),
        ("daily", "day"),
        ("WEEK", "week"),
        ("weekly", "week"),
        ("month", "month"),
        ("monthly", "month"),
        ("never", "never"),
        ("none", "never"),
    ],
)
def test_reset_period_parses_calendar_periods(text: str, expected: str) -> None:
    assert parse_traffic_reset(text) == expected


@pytest.mark.parametrize("text", ["hourly", "year", "month_rolling", ""])
def test_reset_period_refuses_other_periods(text: str) -> None:
    with pytest.raises(ClientLimitError):
        parse_traffic_reset(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-12-31", "2026-12-31T23:59:59Z"),
        ("2026-12-31T18:00:00Z", "2026-12-31T18:00:00Z"),
        ("2026-12-31T18:00:00+03:00", "2026-12-31T15:00:00Z"),
        ("2026-12-31T18:00", "2026-12-31T18:00:00Z"),
        ("2026-12-31T18:00:00.750Z", "2026-12-31T18:00:00Z"),
        ("۲۰۲۶-۱۲-۳۱", "2026-12-31T23:59:59Z"),
        ("never", None),
        ("NEVER", None),
    ],
    ids=["date-is-end-of-day-utc", "utc", "offset", "naive-is-utc", "fraction", "persian-digits", "never", "upper"],
)
def test_expiry_parses_dates_timestamps_and_never(text: str, expected: str | None) -> None:
    assert parse_expiry(text, now=NOW) == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("2026-10-28", "in the past"),
        ("2026-10-29T11:00:00Z", "in the past"),
        ("2099-01-01", "too far ahead"),
        ("31.12.2026", "not a date"),
        ("2026-02-30", "not a date"),
        ("tomorrow", "not a date"),
    ],
    ids=["yesterday", "earlier-today", "never-sentinel-year", "dotted", "impossible-day", "word"],
)
def test_expiry_refuses_past_far_and_unreadable_dates(text: str, message: str) -> None:
    with pytest.raises(ClientLimitError, match=message):
        parse_expiry(text, now=NOW)


def test_today_is_still_a_valid_expiry_until_its_end() -> None:
    assert parse_expiry("2026-10-29", now=NOW) == "2026-10-29T23:59:59Z"


def test_limit_change_carries_only_the_options_given() -> None:
    assert build_limit_change().is_empty
    change = build_limit_change(expires="never", now=NOW)
    assert change.sets_expiry and change.expires_at is None
    assert change.traffic_limit_bytes is None and change.traffic_reset is None
    assert not build_limit_change(limit="50GB").sets_expiry


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-12-31T23:59:59.000Z", "2026-12-31T23:59:59Z"),
        ("2026-12-31T15:00:00+00:00", "2026-12-31T15:00:00Z"),
        (NEVER_EXPIRES_AT, ""),
        ("", ""),
        ("garbage", ""),
    ],
    ids=["panel-millis", "offset", "never-sentinel", "empty", "unreadable"],
)
def test_panel_expiry_reads_as_one_utc_form(raw: str, expected: str) -> None:
    assert canonical_expiry(raw) == expected


def test_remnawave_limits_patch_names_only_the_changed_fields() -> None:
    fake = FakeRemnawave("alice")
    with fake.panel() as panel:
        user = panel.get_user("alice")
        assert user is not None
        updated = panel.update_user_limits(
            user.id, build_limit_change(limit="50GB", reset="month", expires="2031-12-31", now=NOW)
        )
        panel.update_user_limits(user.id, ClientLimitChange(traffic_limit_bytes=0))
        panel.update_user_limits(user.id, build_limit_change(expires="never"))
        reset = panel.reset_user_traffic(user.id)

    patches = [body for method, _path, body in fake.requests if method == "PATCH"]
    assert patches == [
        {"id": 101, "trafficLimitBytes": 50 * GB, "trafficLimitStrategy": "MONTH", "expireAt": "2031-12-31T23:59:59Z"},
        {"id": 101, "trafficLimitBytes": 0},
        # Every panel user needs an expiry; "never" is Meridian's far-future date.
        {"id": 101, "expireAt": NEVER_EXPIRES_AT},
    ]
    assert fake.requests[-1][:2] == ("POST", "/api/users/101/actions/reset-traffic")
    assert (updated.traffic_limit_strategy, updated.expire_at) == ("MONTH", "2031-12-31T23:59:59Z")
    assert reset.used_traffic_bytes == 0
    assert build_client_record(reset).expires_at == ""


@pytest.mark.parametrize(
    ("reset", "strategy"),
    [("day", "DAY"), ("week", "WEEK"), ("month", "MONTH"), ("never", "NO_RESET")],
)
def test_remnawave_reset_periods_use_the_panel_enum(reset: str, strategy: str) -> None:
    fake = FakeRemnawave("alice")
    with fake.panel() as panel:
        panel.update_user_limits("101", build_limit_change(reset=reset))
    assert fake.requests[-1][2] == {"id": 101, "trafficLimitStrategy": strategy}


# --- Control-service adapter ----------------------------------------------------------------------------------


def test_control_service_limits_round_trip_through_the_access_panel(tmp_path: Path) -> None:
    with serve_control(tmp_path) as url:
        with ControlClient(url, ADMIN_TOKEN) as client:
            client.put_user("alice")
        with ServiceAccessPanel(url, ADMIN_TOKEN) as panel:
            user = panel.get_user("alice")
            assert user is not None
            limited = panel.update_user_limits(
                user.id, build_limit_change(limit="50GB", reset="week", expires=FUTURE_DAY)
            )
            cleared = panel.update_user_limits(user.id, build_limit_change(limit="none", expires="never"))
            reset = panel.reset_user_traffic(user.id)

    first = build_client_record(limited)
    assert (first.traffic_limit_bytes, first.traffic_reset, first.expires_at) == (
        50 * GB,
        "week",
        f"{FUTURE_DAY}T23:59:59Z",
    )
    second = build_client_record(cleared)
    # Omitted fields keep their value: the reset period stays.
    assert (second.traffic_limit_bytes, second.traffic_reset, second.expires_at) == (0, "week", "")
    assert reset.status == "ACTIVE"


# --- Applies leave limits alone --------------------------------------------------------------------------------


def test_control_service_apply_keeps_limits_set_by_the_operator(tmp_path: Path) -> None:
    """The control-user driver converges on existence alone, and re-applying never resets limits or status."""
    from meridian.compiler import compile_topology
    from meridian.reconciler.meridian_drivers import MeridianDriverContext, build_meridian_drivers
    from meridian.reconciler.resources import build_resource_actions, observation_converges
    from meridian.reconciler.workloads import LocalRealityKeyFactory, WorkloadStateManager
    from tests.support.mock_connection import MockConnection

    plan = compile_topology(
        single_server_intent(None, server_ref="srv-a", address="198.51.100.10", client_name="alice", control="meridian")
    )
    action = next(item for item in build_resource_actions(plan, 1) if item.resource.payload.kind == "control_user")
    with serve_control(tmp_path) as url:
        cluster = ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN))
        driver = build_meridian_drivers(
            MeridianDriverContext(
                plan=plan,
                cluster=cluster,
                client=lambda: ControlClient(url, ADMIN_TOKEN),
                workloads=WorkloadStateManager(cluster, persist=lambda _c: None, key_factory=LocalRealityKeyFactory()),
                connection_for=lambda _ref: MockConnection(),
                server_addresses={"srv-a": "198.51.100.10"},
                agent_binary=lambda _arch: MagicMock(),
                sleep=lambda _s: None,
            )
        )["control_user"]
        driver.apply(action, None)
        with ServiceAccessPanel(url, ADMIN_TOKEN) as panel:
            user = panel.get_user("alice")
            assert user is not None
            panel.update_user_limits(user.id, build_limit_change(limit="50GB", reset="month", expires=FUTURE_DAY))
            panel.disable_user(user.id)

        assert observation_converges(action, driver.observe(action, None))
        driver.apply(action, None)
        with ServiceAccessPanel(url, ADMIN_TOKEN) as panel:
            after = panel.get_user("alice")

    assert after is not None
    record = build_client_record(after)
    assert (record.status, record.traffic_limit_bytes, record.traffic_reset, record.expires_at) == (
        "DISABLED",
        50 * GB,
        "month",
        f"{FUTURE_DAY}T23:59:59Z",
    )


def test_remnawave_access_user_update_never_sends_operator_owned_fields() -> None:
    """The reconciler's only user PATCH carries identity and squads, so limits, expiry, and status survive apply."""
    fake = FakeRemnawave("alice")
    with fake.panel() as panel:
        panel.update_user_limits("101", build_limit_change(limit="50GB", reset="month", expires=FUTURE_DAY))
        updated = panel.update_access_user("101", squad_uuids=["00000000-0000-0000-0000-000000000004"])

    assert set(fake.requests[-1][2]) == {"id", "activeInternalSquads", "externalSquadUuid"}
    record = build_client_record(updated)
    assert (record.traffic_limit_bytes, record.traffic_reset, record.expires_at) == (
        50 * GB,
        "month",
        f"{FUTURE_DAY}T23:59:59Z",
    )
