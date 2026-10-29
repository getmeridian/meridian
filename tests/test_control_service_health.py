"""Health primitives for Meridian's control service: systemd units and each node agent's report."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from meridian.control.models import NodeStatus
from meridian.core.agent_types import NodeStateRef
from meridian.diagnostics.control_service import (
    agent_finding,
    agent_version_finding,
    check_unit_active,
    service_version_finding,
)
from meridian.diagnostics.logs import log_sources
from tests.support.mock_connection import MockConnection

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
HINT = "meridian logs 198.51.100.10 --component node"


def _status(**changes: object) -> NodeStatus:
    fields: dict[str, object] = {
        "node_id": "main",
        "fleet_epoch": 1,
        "revision": 3,
        "sha256": "a" * 64,
        "spec_sha256": "b" * 64,
        "applied": NodeStateRef(fleet_epoch=1, revision=3, sha256="a" * 64),
        "last_seen_at": "2026-10-29T11:59:30Z",
        "agent_version": "0.1.0",
    }
    return NodeStatus.model_validate({**fields, **changes})


@pytest.mark.parametrize(
    ("status", "expected", "message"),
    [
        (_status(), "passed", "applied revision 3"),
        (None, "failed", "not registered"),
        (_status(last_seen_at=None, applied=None), "failed", "never reached"),
        (_status(last_seen_at="2026-10-29T11:49:30.25Z"), "failed", "10 min ago"),
        (_status(failed=["core-config"], errors=["core-config: bad json"]), "failed", "core-config: bad json"),
        (_status(applied=NodeStateRef(fleet_epoch=1, revision=2, sha256="c" * 64)), "warning", "revision 2 of 3"),
        (_status(applied=None), "warning", "revision none of 3"),
    ],
    ids=["current", "unregistered", "never-seen", "stale", "failed-resource", "behind", "never-applied"],
)
def test_agent_finding_judges_the_services_record(status: NodeStatus | None, expected: str, message: str) -> None:
    found = agent_finding(status, logs_hint=HINT, now=NOW)

    assert found.status == expected
    assert message in found.message


@pytest.mark.parametrize(
    ("stdout", "rc", "expected"),
    [("active\n", 0, "passed"), ("inactive\n", 3, "failed"), ("failed\n", 3, "failed"), ("", 255, "skipped")],
    ids=["active", "inactive", "failed", "ssh-lost"],
)
def test_unit_state_is_evidence_only_when_systemctl_answered(stdout: str, rc: int, expected: str) -> None:
    conn = MockConnection().when("systemctl is-active", stdout=stdout, rc=rc)

    result = check_unit_active(conn, "netcore.service", remediation="fix it")

    assert result.status == expected
    assert conn._calls == ["systemctl is-active netcore.service"]


def test_control_service_logs_come_from_units_not_containers() -> None:
    sources = log_sources(["control", "exit"])

    assert [(source.component, source.kind, source.name) for source in sources] == [
        ("node", "unit", "netagent.service"),
        ("node", "unit", "netcore.service"),
        ("panel", "unit", "ctld.service"),
        ("nginx", "file", "/var/log/nginx/error.log"),
    ]


@pytest.mark.parametrize(
    ("service", "expected", "message"),
    [
        ("4.0.0", "passed", "runs this Meridian"),
        ("4.0.0b8", "warning", "upgrades it"),
        ("4.1.0", "failed", "cannot apply"),
        ("weird!", "skipped", "Cannot compare"),
    ],
    ids=["same", "older", "newer", "unparsable"],
)
def test_service_version_finding_follows_the_skew_rules(service: str, expected: str, message: str) -> None:
    found = service_version_finding("4.0.0", service)

    assert found.status == expected
    assert message in found.message


@pytest.mark.parametrize(
    ("expected", "status", "warns"),
    [
        ("0.2.0", _status(agent_version="0.1.0"), True),
        ("0.2.0", _status(agent_version="0.2.0"), False),
        ("", _status(agent_version="0.1.0"), False),
        ("0.2.0", _status(agent_version=None, last_seen_at=None), False),
        ("0.2.0", None, False),
    ],
    ids=["behind", "current", "unknown-build", "never-reported", "unregistered"],
)
def test_agent_version_finding_warns_only_on_a_known_difference(
    expected: str, status: NodeStatus | None, warns: bool
) -> None:
    found = agent_version_finding(expected, status)

    assert (found is not None and found.status == "warning") is warns
