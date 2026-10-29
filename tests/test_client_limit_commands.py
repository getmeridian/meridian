"""`client set`, `client reset-traffic`, and limits in `client add` and `client show`."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.client import run_add, run_enable, run_list, run_show
from meridian.commands.client_handoff import PageResolution
from meridian.commands.client_limits import format_expiry, format_usage, run_reset_traffic, run_set
from meridian.console import set_json_mode
from meridian.control.client import ControlClient
from meridian.control.models import UserUpdate
from meridian.core.clients import build_client_record
from meridian.core.schema import schema_model
from meridian.handoff import HandoffLinks
from meridian.remnawave import User
from meridian.setup.single_server import single_server_intent
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.topology import single_exit_intent

GB = 1024**3
FUTURE_DAY = "2031-12-31"


# --- Display ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expires_at", "shown"),
    [("2026-12-31T23:59:59Z", "2026-12-31"), ("2026-12-31T15:00:00Z", "2026-12-31 15:00 UTC"), ("", "never")],
    ids=["end-of-day", "time", "never"],
)
def test_expiry_display(expires_at: str, shown: str) -> None:
    assert format_expiry(expires_at) == shown


@pytest.mark.parametrize(
    ("strategy", "limit", "shown"),
    [
        ("MONTH", 50 * GB, "12.4 GB / 50 GB (resets monthly)"),
        ("NO_RESET", 50 * GB, "12.4 GB / 50 GB"),
        ("", 0, "12.4 GB"),
        ("MONTH_ROLLING", 50 * GB, "12.4 GB / 50 GB (resets monthly on the day they were added)"),
    ],
    ids=["monthly", "no-reset", "unlimited", "panel-rolling"],
)
def test_usage_line(strategy: str, limit: int, shown: str) -> None:
    user = User(used_traffic_bytes=int(12.4 * GB), traffic_limit_bytes=limit, traffic_limit_strategy=strategy)
    assert format_usage(build_client_record(user)) == shown


# --- Commands against a control service -------------------------------------------------------------------------


def _mocked_cluster(tmp_path: Path) -> ClusterConfig:
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/panel", api_token="test-token", server_ip="198.51.100.1"),
        topology_intent=single_exit_intent(users=["alice", "bob"]),
    )
    cluster.save(tmp_path / "cluster.yml")
    return cluster


@contextmanager
def _fleet(tmp_path: Path, *, json_mode: bool = False) -> Iterator[SimpleNamespace]:
    """A saved fleet on a real loopback control service with client alice, served to client commands."""
    set_json_mode(json_mode)
    try:
        with ExitStack() as stack:
            url = stack.enter_context(serve_control(tmp_path))
            cluster = ClusterConfig(
                panel=PanelConfig(url=url, api_token=ADMIN_TOKEN, server_ip="198.51.100.1"),
                topology_intent=single_server_intent(None, server_ref="srv-a", client_name="alice"),
            )
            client = stack.enter_context(ControlClient(url, ADMIN_TOKEN))
            client.put_user("alice")
            stack.enter_context(patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster))
            yield SimpleNamespace(cluster=cluster, create=client.put_user)
    finally:
        set_json_mode(False)


def _envelope(capsys: pytest.CaptureFixture[str], schema: str) -> dict[str, Any]:
    payload = json.loads(capsys.readouterr().out)
    schema_model(schema).model_validate(payload)
    return payload


def test_client_set_changes_and_clears_limits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with _fleet(tmp_path, json_mode=True):
        run_set("alice", limit="50GB", reset="month", expires=FUTURE_DAY)
        first = _envelope(capsys, "client-set-envelope")
        run_set("alice", limit="none", reset=None, expires="never")
        second = _envelope(capsys, "client-set-envelope")

    assert first["command"] == "client.set" and first["summary"]["changed"] is True
    client = first["data"]["client"]
    assert (client["username"], client["status"].lower()) == ("alice", "active")
    assert (client["traffic_limit_bytes"], client["traffic_reset"], client["expires_at"]) == (
        50 * GB,
        "month",
        f"{FUTURE_DAY}T23:59:59Z",
    )
    client = second["data"]["client"]
    assert (client["traffic_limit_bytes"], client["traffic_reset"], client["expires_at"]) == (0, "month", "")


def test_client_reset_traffic_starts_usage_from_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with _fleet(tmp_path, json_mode=True):
        run_reset_traffic("alice")
        payload = _envelope(capsys, "client-reset-traffic-envelope")

    assert payload["command"] == "client.reset-traffic"
    assert payload["data"]["client"]["traffic_used_bytes"] == 0


def test_client_show_prints_usage_against_the_limit_and_the_expiry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with _fleet(tmp_path):
        run_set("alice", limit="50GB", reset="month", expires=FUTURE_DAY)
        capsys.readouterr()
        with patch("meridian.commands.client.resolve_page_url", return_value=PageResolution()):
            run_show("alice")

    output = capsys.readouterr().err
    assert "Traffic   " in output and "/ 50 GB (resets monthly)" in output
    assert f"Expires   {FUTURE_DAY}" in output


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({}, "Nothing to change"),
        ({"limit": "lots"}, "is not a size"),
        ({"expires": "2020-01-01"}, "in the past"),
        ({"reset": "hourly"}, "is not one of"),
    ],
    ids=["nothing", "bad-limit", "past-expiry", "bad-period"],
)
def test_client_set_refuses_bad_input_before_any_connection(
    options: dict[str, str], message: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    arguments = {"limit": None, "reset": None, "expires": None, **options}
    with (
        _fleet(tmp_path, json_mode=True),
        patch("meridian.commands.client_limits.access_panel") as access_panel,
        pytest.raises(typer.Exit) as exc_info,
    ):
        run_set("alice", **arguments)

    payload = json.loads(capsys.readouterr().out)
    assert exc_info.value.exit_code == 2
    assert message in payload["errors"][0]["message"]
    access_panel.assert_not_called()


def test_client_set_refuses_a_name_that_is_not_a_fleet_client(tmp_path: Path) -> None:
    with _fleet(tmp_path), pytest.raises(typer.Exit) as exc_info:
        run_set("mallory", limit="50GB", reset=None, expires=None)
    assert exc_info.value.exit_code == 2


def test_client_add_sets_limits_after_the_reviewed_apply(
    tmp_path: Path, tmp_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with _fleet(tmp_path, json_mode=True) as fleet:
        runtime = MagicMock()

        def apply_intent(intent: Any, *_args: object, **_kwargs: object) -> SimpleNamespace:
            fleet.create("carol")
            return SimpleNamespace(all_succeeded=True, changed=True, failed=[])

        runtime.apply_intent.side_effect = apply_intent
        with (
            patch("meridian.setup.runtime.SetupRuntime", return_value=runtime),
            patch("meridian.servers.ServerRegistry"),
            patch(
                "meridian.commands.client.publish_connection_pages",
                return_value=[HandoffLinks("carol", subscription_url="https://198.51.100.1/pages/sub/short-carol")],
            ),
        ):
            run_add(["carol"], limit="50GB", reset="month", expires=FUTURE_DAY)
        payload = _envelope(capsys, "client-add-envelope")
        with access_panel_for(fleet.cluster) as panel:
            carol = build_client_record(panel.get_user("carol"))  # type: ignore[arg-type]

    assert payload["status"] == "ok" and payload["warnings"] == []
    assert (carol.traffic_limit_bytes, carol.traffic_reset, carol.expires_at) == (
        50 * GB,
        "month",
        f"{FUTURE_DAY}T23:59:59Z",
    )


def access_panel_for(cluster: ClusterConfig) -> Any:
    from meridian.control_access import access_panel

    return access_panel(cluster)


def test_client_add_reports_limits_it_could_not_set_as_a_partial_result(
    tmp_path: Path, tmp_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from meridian.remnawave import RemnawaveError

    panel = MagicMock()
    panel.__enter__.return_value = panel
    panel.get_user.side_effect = [None, User(id="102", username="carol", short_uuid="short-carol", status="ACTIVE")]
    panel.update_user_limits.side_effect = RemnawaveError("Panel API error: 400")
    runtime = MagicMock()
    runtime.apply_intent.return_value = SimpleNamespace(all_succeeded=True, changed=True, failed=[])
    set_json_mode(True)
    try:
        with (
            patch("meridian.commands._helpers.ClusterConfig.load", return_value=_mocked_cluster(tmp_path)),
            patch("meridian.control_access.ServiceAccessPanel", return_value=panel),
            patch("meridian.setup.runtime.SetupRuntime", return_value=runtime),
            patch("meridian.servers.ServerRegistry"),
            patch("meridian.commands.client.publish_connection_pages", return_value=[]),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_add(["carol"], limit="50GB")
    finally:
        set_json_mode(False)

    payload = json.loads(capsys.readouterr().out)
    assert exc_info.value.exit_code == 3
    assert payload["data"]["clients"][0]["username"] == "carol"
    assert payload["warnings"][0]["code"] == "MERIDIAN_CLIENT_LIMITS_FAILED"
    assert "meridian client set carol" in payload["warnings"][0]["hint"]


def test_client_list_shows_console_people_marked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A control service holds only this fleet's people, so one added in its console is listed, marked."""
    with _fleet(tmp_path, json_mode=True) as fleet:
        fleet.create("carol")
        run_list()
        clients = _envelope(capsys, "client-list-envelope")["data"]["clients"]
        set_json_mode(False)
        run_list()

    assert {client["username"]: client["declared"] for client in clients} == {"alice": True, "carol": False}
    assert "carol · console" in capsys.readouterr().err


@pytest.mark.parametrize("username", ["ab", "a.b", "c" * 37], ids=["two-chars", "dotted", "37-chars"])
def test_client_show_reads_a_console_name_outside_remnawaves_rules(
    username: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control service allows names Remnawave never did; `client list` shows them, so `show` must too."""
    with _fleet(tmp_path, json_mode=True) as fleet:
        fleet.create(username)
        with patch("meridian.commands.client.resolve_page_url", return_value=PageResolution()):
            run_show(username)
        client = _envelope(capsys, "client-show-envelope")["data"]["client"]

    assert (client["username"], client["declared"]) == (username, False)


def test_client_show_prefers_an_exact_console_name_and_refuses_an_ambiguous_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Declared `alice` and console `ALICE` are two people; a case-folded lookup handed out alice's link."""
    with _fleet(tmp_path, json_mode=True) as fleet:
        fleet.create("ALICE")
        with patch("meridian.commands.client.resolve_page_url", return_value=PageResolution()):
            run_show("ALICE")
            client = _envelope(capsys, "client-show-envelope")["data"]["client"]
            with pytest.raises(typer.Exit) as refused:
                run_show("Alice")

    assert (client["username"], client["declared"]) == ("ALICE", False)
    assert refused.value.exit_code == 2
    assert "could mean ALICE, alice" in capsys.readouterr().out


def test_enabling_an_expired_client_reports_that_it_stays_expired(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The service re-applies expiry at once; the command once claimed `active` all the same."""
    with _fleet(tmp_path, json_mode=True) as fleet:
        with ControlClient(fleet.cluster.panel.url, ADMIN_TOKEN) as client:
            client.update_user("alice", UserUpdate(status="disabled", expire_at="2020-01-01T00:00:00Z"))
        run_enable("alice")
        payload = _envelope(capsys, "client-enable-envelope")

    assert payload["data"]["client"] == {"username": "alice", "status": "expired"}
