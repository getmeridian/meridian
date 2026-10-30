"""The control service's bootstrap driver and the subscription check verification uses."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from meridian.compiler.models import ControlServicePayload, make_resource
from meridian.reconciler.control_service_driver import (
    ControlServiceDriver,
    ControlServiceDriverContext,
    xray_subscription_is_valid,
)
from meridian.reconciler.resources import (
    ResourceReconcileError,
    UnknownResourceOutcome,
    assert_complete_driver_registry,
    build_resource_actions,
    observation_converges,
)
from tests.support.topology import resource_plan


def _control_action():
    resource = make_resource("control:service", ControlServicePayload(server_ref="srv-control"))
    return build_resource_actions(resource_plan(resource), 1)[0]


def test_control_service_bootstraps_then_requires_readiness() -> None:
    action = _control_action()
    ready = False

    def bootstrap(_action) -> None:
        nonlocal ready
        ready = True

    driver = ControlServiceDriver(ControlServiceDriverContext(ready=lambda _action: ready, bootstrap=bootstrap))

    assert not observation_converges(action, driver.observe(action, None))
    driver.apply(action, None)
    assert observation_converges(action, driver.observe(action, None))


def test_control_service_preserves_unknown_outcome_on_bootstrap_timeout() -> None:
    def timeout(_action) -> None:
        raise TimeoutError("bootstrap timed out")

    driver = ControlServiceDriver(ControlServiceDriverContext(ready=lambda _action: False, bootstrap=timeout))

    with pytest.raises(UnknownResourceOutcome, match="observation is required"):
        driver.apply(_control_action(), None)


def test_production_driver_registry_requires_every_applied_kind() -> None:
    with pytest.raises(ResourceReconcileError, match="missing .*control_service"):
        assert_complete_driver_registry({})


def _document(content: str) -> SimpleNamespace:
    return SimpleNamespace(url="https://198.51.100.10/sub/short-access", content=content)


def _config(*outbounds: dict) -> dict:
    socks = {"tag": "SOCKS", "listen": "127.0.0.1", "port": 1080, "protocol": "socks"}
    return {"inbounds": [socks], "outbounds": list(outbounds)}


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (json.dumps([_config({"tag": "MERIDIAN_PROXY_1", "protocol": "vless"})]), True),
        ("<html>stale proxy page</html>", False),
        (json.dumps([{"outbounds": [{"tag": "MERIDIAN_PROXY", "protocol": "vless"}]}] * 2), False),
        (
            json.dumps([_config({"tag": "MERIDIAN_PROXY_HY2", "protocol": "hysteria", "settings": {"version": 2}})]),
            True,
        ),
        (
            json.dumps([_config({"tag": "MERIDIAN_PROXY_HY2", "protocol": "hysteria", "settings": {"version": 1}})]),
            False,
        ),
        (
            json.dumps([_config({"tag": "MERIDIAN_PROXY_HY2", "protocol": "hysteria2", "settings": {"version": 2}})]),
            False,
        ),
    ],
    ids=["one-proxy", "html", "two-configs", "hysteria-v2", "hysteria-v1", "hysteria2-protocol"],
)
def test_subscription_check_requires_one_canonical_xray_config(content: str, expected: bool) -> None:
    assert xray_subscription_is_valid(_document(content)) is expected
