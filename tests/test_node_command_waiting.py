"""One bounded wait covers every node, preserves partial results, and distinguishes core fallback."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from meridian.control.client import ControlServiceError, ControlServiceUncertain
from meridian.control.command_models import NodeCommandOutcome
from meridian.node_commands import command_exit_code, command_warnings, wait_for_commands, with_command_issue_error
from tests.support.node_commands import command_record


def test_waiting_returns_each_typed_node_outcome() -> None:
    finals = [
        command_record("done", "succeeded", index=1),
        command_record("fallback", "succeeded", index=2, fallback=True),
        command_record("failed", "failed", index=3),
        command_record("timeout", "expired", index=4),
    ]
    by_id = {record.command.command_id: record for record in finals}
    client = MagicMock()
    client.command.side_effect = lambda node, identifier: by_id[identifier]
    pending = [command_record(record.node_id, index=index) for index, record in enumerate(finals, 1)]
    outcomes = wait_for_commands(client, pending)
    assert {outcome.node_id: outcome.status for outcome in outcomes} == {
        "done": "done",
        "fallback": "core_restart",
        "failed": "failed",
        "timeout": "timed_out",
    }
    assert all(len(outcome.commands) == 1 for outcome in outcomes)
    assert command_exit_code(outcomes) == 4


def test_offline_nodes_share_one_timeout_and_are_all_reported() -> None:
    now = 0.0

    def clock() -> float:
        return now

    def sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    pending = [command_record(f"node-{index}", index=index) for index in range(1, 4)]
    by_id = {record.command.command_id: record for record in pending}
    client = MagicMock()
    client.command.side_effect = lambda node, identifier: by_id[identifier]
    outcomes = wait_for_commands(client, pending, timeout=2, clock=clock, sleep=sleep)
    assert now == 2 and len(outcomes) == 3
    assert all(outcome.status == "timed_out" for outcome in outcomes)
    assert command_exit_code(outcomes) == 3


def test_one_failed_inbound_preserves_the_other_inbounds_success() -> None:
    succeeded = command_record("main", "succeeded", fallback=True, index=1)
    failed = command_record("main", "failed", index=2)
    result = wait_for_commands(MagicMock(), [succeeded, failed])[0]
    assert result.status == "failed" and len(result.commands) == 2
    assert result.commands[0].result.effect == "core_restart"


@pytest.mark.parametrize("earlier", [None, "done", "core_restart", "timed_out", "failed"])
def test_review_3_3_uncertain_issues_are_retryable_unless_a_node_already_failed(earlier: str | None) -> None:
    previous = NodeCommandOutcome(node_id="main", status=earlier) if earlier is not None else None
    error = ControlServiceUncertain("The issue may have committed.", retryable=True)
    outcome = with_command_issue_error("main", previous, error)
    assert outcome.status == ("failed" if earlier == "failed" else "timed_out")
    warning = command_warnings([outcome])[0]
    assert warning.retryable is (earlier != "failed")
    assert command_exit_code([outcome]) == (4 if earlier == "failed" else 3)


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_review_5_2_non_retryable_poll_errors_fail_and_keep_finished_siblings(status: int) -> None:
    done = command_record("done", "succeeded", index=1, fallback=True)
    unreadable = command_record("unreadable", index=2)
    sibling = command_record("sibling", index=3)
    client = MagicMock()

    def read(node, identifier):
        if node == "unreadable":
            raise ControlServiceError(f"The result was refused: HTTP {status}", category="user", retryable=False)
        return command_record("sibling", "succeeded", index=3)

    client.command.side_effect = read
    outcomes = wait_for_commands(client, [done, unreadable, sibling])
    by_node = {outcome.node_id: outcome for outcome in outcomes}
    assert by_node["unreadable"].status == "failed"
    assert by_node["done"].status == "core_restart" and by_node["sibling"].status == "done"
    assert by_node["unreadable"].errors == [f"The result was refused: HTTP {status}"]
    assert by_node["unreadable"].commands == [unreadable]
    assert client.command.call_count == 2 and command_exit_code(outcomes) == 4
    assert command_warnings(outcomes)[0].retryable is False
