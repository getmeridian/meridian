"""The exporter has a narrow explicit grant and never exposes request paths or people."""

import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from meridian.control.models import NodeSpec
from meridian.control.times import format_timestamp
from meridian.control.token_capabilities import capability_catalog
from meridian.control.tokens import WEB_OWNER
from meridian.core.agent_protocol import ObservedStateReport, StatsReport, UserTraffic
from meridian.core.token_models import TokenCreate
from tests.support.capacity import capacity_report
from tests.support.control_service import ADMIN_TOKEN
from tests.support.token_service import serve_tokens, token_request


def scrape(url: str, token: str = "") -> tuple[int, str, dict[str, str]]:
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        response = urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, response.read().decode(), dict(response.headers)


def test_metrics_requires_authentication_like_other_control_routes(tmp_path: Path) -> None:
    with serve_tokens(tmp_path) as fleet:
        status, body, _ = scrape(fleet.url + "/metrics")
        assert status == 401
        assert body == scrape(fleet.url + "/api/health")[1]


def test_metrics_grant_preview_is_explicit_and_read_only(tmp_path: Path) -> None:
    with serve_tokens(tmp_path) as fleet:
        request = {"name": "Scraper", "capabilities": ["metrics.read"]}
        status, preview = token_request(fleet.url + "/api/tokens/preview", "POST", request, ADMIN_TOKEN)
        assert status == 200, preview
        assert preview["capabilities"] == ["metrics.read"]
        _, issued = token_request(fleet.url + "/api/tokens", "POST", request, ADMIN_TOKEN)
        status, text, headers = scrape(fleet.url + "/metrics", issued["token"])
        assert status == 200 and headers["Content-Type"].startswith("text/plain; version=0.0.4")
        assert "meridian_control_sqlite_bytes" in text
        for path in ("/api/health", "/api/users", "/api/settings"):
            assert token_request(fleet.url + path, token=issued["token"])[0] == 403
        assert token_request(fleet.url + "/api/users", "POST", {"username": "sample"}, issued["token"])[0] == 403


@pytest.mark.parametrize("scope", ["read", "people", "admin"])
def test_compatible_presets_do_not_gain_metrics(tmp_path: Path, scope: str) -> None:
    with serve_tokens(tmp_path) as fleet:
        _, issued = token_request(fleet.url + "/api/tokens", "POST", {"name": "Existing", "scope": scope}, ADMIN_TOKEN)
        assert "metrics.read" not in issued["capabilities"]
        assert scrape(fleet.url + "/metrics", issued["token"])[0] == 403


def test_metrics_preserves_zero_and_omits_stale_capacity(tmp_path: Path) -> None:
    with serve_tokens(tmp_path) as fleet:
        fleet.service.put_node_spec("main", NodeSpec(resources=[]))
        fleet.service.node_seen("main", "0.2.17", capabilities=["capacity_telemetry"])
        raw = capacity_report(load1_milli=0, memory_available_bytes=0)
        fleet.service.report("main", ObservedStateReport.model_validate(raw), raw)
        status, text, _ = scrape(fleet.url + "/metrics", ADMIN_TOKEN)
        assert status == 200
        assert 'meridian_node_memory_available_bytes{node="main"} 0\n' in text
        assert 'meridian_node_capacity_status{node="main",state="current"} 1\n' in text
        assert "meridian_node_host_receive_bytes_per_second{" not in text
        fleet.clock.now += 180
        fleet.service.node_seen("main", "0.2.17", capabilities=["capacity_telemetry"])
        _, text, _ = scrape(fleet.url + "/metrics", ADMIN_TOKEN)
        assert 'meridian_node_capacity_status{node="main",state="stale"} 1\n' in text
        assert "meridian_node_memory_available_bytes{" not in text


def test_runtime_metrics_count_routes_without_storing_request_text(tmp_path: Path) -> None:
    with serve_tokens(tmp_path) as fleet:
        for _ in range(2):
            token_request(fleet.url + "/api/health?private-query-canary=1", token=ADMIN_TOKEN)
        token_request(fleet.url + "/private-path-canary", token=ADMIN_TOKEN)
        fleet.service.background.enforcement.failed()
        status, text, _ = scrape(fleet.url + "/metrics", ADMIN_TOKEN)
        assert status == 200
        assert 'meridian_control_requests_total{route_id="health",status_class="2xx"} 2\n' in text
        assert 'meridian_control_requests_total{route_id="unmatched",status_class="4xx"} 1\n' in text
        assert (
            'meridian_control_request_duration_seconds_bucket{route_id="health",status_class="2xx",le="+Inf"} 2\n'
            in text
        )
        assert 'meridian_control_background_task_error{task="enforcement"} 1\n' in text
        size = re.search(r"^meridian_control_sqlite_bytes (\d+)$", text, re.MULTILINE)
        assert size is not None and int(size[1]) > 0
        assert "private-query-canary" not in text and "private-path-canary" not in text


def test_metrics_never_carries_person_labels_credentials_or_urls(tmp_path: Path) -> None:
    with serve_tokens(tmp_path) as fleet:
        fleet.service.put_node_spec("main", NodeSpec(resources=[]))
        person = fleet.service.put_user("person-canary")
        fleet.service.record_stats(
            "main",
            StatsReport(
                counter_epoch="66a5b659-5aad-425c-a786-512ad6a86ac1",
                seq=1,
                collected_at=format_timestamp(fleet.clock()),
                users=[UserTraffic(user_id=person.user_id, uplink_bytes=100, downlink_bytes=200)],
            ),
        )
        token_request(fleet.url + "/api/users/person-canary", token=ADMIN_TOKEN)
        status, text, _ = scrape(fleet.url + "/metrics", ADMIN_TOKEN)
        assert status == 200
        assert 'meridian_node_counted_traffic_bytes_total{node="main"} 300\n' in text
        labels = set(re.findall(r'([a-z_]+)="', text))
        assert labels <= {"node", "state", "route_id", "status_class", "le", "task"}
        for value in (person.username, person.uuid, person.sub_token, person.user_id, fleet.url):
            assert value not in text
        assert "/api/" not in text and "/sub/" not in text and "https://" not in text


def test_metrics_grants_are_cataloged_and_console_delegable_without_expanding_presets(tmp_path: Path) -> None:
    with serve_tokens(tmp_path) as fleet:
        row = next(row for row in capability_catalog().capabilities if row.capability == "metrics.read")
        assert row.console_delegable
        assert [(operation.method, operation.path) for operation in row.operations] == [("GET", "/metrics")]
        preview = fleet.service.tokens.preview(
            TokenCreate(name="Scraper", capabilities=["metrics.read"]), issuer=WEB_OWNER
        )
        assert preview.capabilities == ["metrics.read"]
        _, old = token_request(fleet.url + "/api/tokens", "POST", {"name": "Existing", "scope": "admin"}, ADMIN_TOKEN)
        status, _ = token_request(
            fleet.url + "/api/tokens/preview", "POST", {"name": "Child", "capabilities": ["metrics.read"]}, old["token"]
        )
        assert status == 403


@pytest.mark.parametrize("ended", ["revoked", "expired"])
def test_metrics_refuses_revoked_and_expired_tokens(tmp_path: Path, ended: str) -> None:
    with serve_tokens(tmp_path) as fleet:
        _, issued = token_request(
            fleet.url + "/api/tokens",
            "POST",
            {"name": "Scraper", "capabilities": ["metrics.read"], "expires_in_days": 1},
            ADMIN_TOKEN,
        )
        assert scrape(fleet.url + "/metrics", issued["token"])[0] == 200
        if ended == "revoked":
            token_request(fleet.url + "/api/tokens/" + issued["id"], "DELETE", token=ADMIN_TOKEN)
        else:
            fleet.clock.now += 86400
        assert scrape(fleet.url + "/metrics", issued["token"])[0] == 401


def test_latency_buckets_and_unknown_ids_are_bounded_and_thread_safe() -> None:
    from concurrent.futures import ThreadPoolExecutor

    from meridian.control.request_metrics import LATENCY_BUCKETS, RequestMetrics

    recorder = RequestMetrics(["health"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: recorder.observe("health", 200, 0.25), range(100)))
    for index in range(100):
        recorder.observe("/private-path-" + str(index), 404, 1)
    rows = recorder.snapshot()
    assert {row.route_id for row in rows} == {"health", "unmatched"}
    health = next(row for row in rows if row.route_id == "health")
    assert health.count == 100 and health.seconds == 25
    assert health.buckets[LATENCY_BUCKETS.index(0.1)] == 0
    assert health.buckets[LATENCY_BUCKETS.index(0.25)] == 100
