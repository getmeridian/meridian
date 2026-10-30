"""One bounded wait covers every node, preserves partial results, and distinguishes core fallback."""

from __future__ import annotations

from unittest.mock import MagicMock

from meridian.node_commands import command_exit_code, wait_for_commands
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
