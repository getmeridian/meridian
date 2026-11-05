"""Setup verification waits for documents that later settings and account writes sign."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.compiler import compile_topology
from meridian.control.models import UserImport, UserImportBatch
from meridian.core.people_query import PeopleFacets, PeoplePage, PeopleQuery
from meridian.reconciler.resources import ResourceReconcileError
from meridian.setup.control_service import verify_control_service
from tests.support.people_cli import people_cli as people_cli
from tests.support.topology import single_exit_intent


def _client(*in_sync: bool) -> MagicMock:
    client = MagicMock()
    client.health.return_value = SimpleNamespace(version="4.0.0")
    client.node.side_effect = [SimpleNamespace(in_sync=value) for value in in_sync]
    client.query_people.return_value = PeoplePage(
        users=[], total=0, population=0, facets=PeopleFacets(), next_cursor=None, observed_at="2026-11-01T12:00:00Z"
    )
    return client


def _verify(client: MagicMock, wait: float = 90.0) -> tuple[str, int, dict[str, str]]:
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.10/control", api_token="test-token", sub_path="pages")
    )
    with (
        patch("meridian.setup.control_service.control_client", return_value=nullcontext(client)),
        patch("meridian.setup.control_service.xray_subscription_is_valid", return_value=True),
        patch("meridian.setup.control_service.endpoint_certificate_errors", return_value=[]),
        patch("meridian.setup.control_service.NODE_APPLY_WAIT_SECONDS", wait),
    ):
        return verify_control_service(compile_topology(single_exit_intent()), cluster)


def test_a_node_still_applying_the_last_account_write_is_awaited_not_failed():
    client = _client(False, False, True)
    version, nodes, _ = _verify(client)
    assert (version, nodes) == ("4.0.0", 1)
    assert client.node.call_count == 3


def test_a_node_that_never_applies_its_document_fails_with_its_name():
    client = _client(*([False] * 50))
    with pytest.raises(ResourceReconcileError, match="Node exit-a has not applied"):
        _verify(client, wait=2.0)


def test_migrated_links_survive_every_status_while_only_active_documents_are_validated(people_cli):
    _runner, cluster, client = people_cli
    statuses = ["active", "disabled", "expired", "limited"]
    client.import_users(
        UserImportBatch(
            users=[
                UserImport(
                    username=status,
                    status=status,
                    uuid=f"00000000-0000-4000-8000-{index:012d}",
                    sub_token=f"sample-token-{index:010d}",
                    created_at="2026-11-01T12:00:00Z",
                    expire_at="2026-10-31T12:00:00Z" if status == "expired" else None,
                    traffic_limit_bytes=1 if status == "limited" else 0,
                    used_traffic_bytes=1 if status == "limited" else 0,
                    lifetime_traffic_bytes=1 if status == "limited" else 0,
                )
                for index, status in enumerate(statuses)
            ]
        )
    )
    observed = MagicMock(wraps=client)
    assert {user.status for user in client.list_users()} == set(statuses)
    observed.node.return_value = SimpleNamespace(in_sync=True)

    def page(query):
        assert query.status == ""
        return client.query_people(PeopleQuery(**{**query.to_data(), "limit": 2}))

    observed.query_people.side_effect = page
    _version, _nodes, urls = _verify(observed)
    assert set(urls) == set(statuses)
    for name in statuses:
        assert urls[name].endswith("/sub/" + client.get_user(name).sub_token)
    observed.fetch_subscription.assert_called_once_with(client.get_user("active").sub_token, document="json")
