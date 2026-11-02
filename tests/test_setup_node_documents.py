"""Setup verification waits for documents that later settings and account writes sign."""

from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.compiler import compile_topology
from meridian.reconciler.resources import ResourceReconcileError
from meridian.setup.control_service import verify_control_service
from tests.support.topology import single_exit_intent


def _client(*in_sync: bool) -> MagicMock:
    client = MagicMock()
    client.health.return_value = SimpleNamespace(version="4.0.0")
    client.node.side_effect = [SimpleNamespace(in_sync=value) for value in in_sync]
    client.get_user.return_value = SimpleNamespace(sub_token="tok")
    return client


def _verify(client: MagicMock, wait: float = 90.0) -> tuple[str, int, dict[str, str]]:
    cluster = ClusterConfig(panel=PanelConfig(url="https://198.51.100.10/control", api_token="test-token"))
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
