"""The person view reports actual node evidence and the remaining transfer boundary."""

from __future__ import annotations

import pytest

from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_person_flow_receipts_show_blocked_pending_and_old_agents_without_writes(locale: str):
    result = run_console(
        "person-flow-blocks",
        locale,
        {
            "share": {
                "flow_blocks": {
                    "username": "sample",
                    "credential_generation": 2,
                    "nodes": [
                        {"node_id": "main", "status": "blocked"},
                        {"node_id": "edge", "status": "pending_node"},
                        {"node_id": "older", "status": "agent_too_old"},
                    ],
                }
            }
        },
    )
    assert result["found"]
    assert result["states"] == ["blocked", "pending_node", "agent_too_old"]
    assert "--cut-off" in result["text"]
    assert "person.flow_" not in result["text"]
    assert result["writes"] == []
