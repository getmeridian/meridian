"""Lifetime traffic and the period start survive service access and client show."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from meridian import i18n
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.client import run_show
from meridian.console import set_json_mode
from meridian.control.client import ControlClient
from meridian.control.models import UserImport, UserImportBatch
from meridian.control.service import utc_now
from meridian.control_access import ServiceAccessPanel
from meridian.core.clients import build_client_show_result
from meridian.setup.single_server import single_server_intent
from tests.support.control_service import ADMIN_TOKEN, serve_control


@pytest.fixture
def fleet(tmp_path: Path) -> Iterator[tuple[ClusterConfig, str]]:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as client:
        stamp = utc_now()
        client.import_users(
            UserImportBatch(
                users=[
                    UserImport(
                        username="sample",
                        uuid="11111111-2222-4333-8444-555555555555",
                        sub_token="sample-subscription-token-01234567",
                        used_traffic_bytes=1024**3,
                        lifetime_traffic_bytes=5 * 1024**3,
                        traffic_reset_at=stamp,
                    )
                ]
            )
        )
        cluster = ClusterConfig(
            panel=PanelConfig(url=url, api_token=ADMIN_TOKEN, server_ip="198.51.100.10"),
            topology_intent=single_server_intent(None, server_ref="srv-a", client_name="sample"),
        )
        yield cluster, stamp


def test_service_access_and_core_detail_keep_lifetime_and_period_start(fleet: tuple[ClusterConfig, str]) -> None:
    cluster, stamp = fleet
    with ServiceAccessPanel(cluster.panel.url, ADMIN_TOKEN) as panel:
        user = panel.get_user("sample")
    assert user is not None
    assert (user.lifetime_used_traffic_bytes, user.last_traffic_reset_at) == (5 * 1024**3, stamp)
    detail = build_client_show_result(user).to_data()["client"]
    assert (detail["lifetime_traffic_bytes"], detail["traffic_reset_at"]) == (5 * 1024**3, stamp)


def test_client_show_json_carries_lifetime_and_period_start(
    fleet: tuple[ClusterConfig, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    cluster, stamp = fleet
    set_json_mode(True)
    try:
        with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
            run_show("sample")
    finally:
        set_json_mode(False)
    detail = json.loads(capsys.readouterr().out)["data"]["client"]
    assert (detail["lifetime_traffic_bytes"], detail["traffic_reset_at"]) == (5 * 1024**3, stamp)


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_client_show_prints_lifetime_and_period_start_in_every_locale(
    fleet: tuple[ClusterConfig, str],
    capsys: pytest.CaptureFixture[str],
    locale: str,
) -> None:
    cluster, stamp = fleet
    i18n.set_locale(locale)
    try:
        with patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster):
            run_show("sample")
    finally:
        i18n.set_locale("en")
    output = capsys.readouterr().err
    catalog = i18n.catalog(locale)
    assert catalog["client.label.lifetime"] in output and "5 GiB" in output
    assert catalog["client.label.period_started"] in output and stamp in output
