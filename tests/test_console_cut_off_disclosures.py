"""Console cut-off reviews name the exact scope on each affected node."""

import pytest

from tests.support.console_dom import needs_console_dom, run_console
from tests.test_cut_off_disclosures import BRIDGE

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_console_reviews_core_and_inbound_reconnects_per_node(locale):
    result = run_console(
        "command-scope-review",
        locale,
        {
            "nodes": [
                {"node_id": "tcp-node", "effect": "core_restart", "inbound_tags": ["vless"]},
                {"node_id": "udp-node", "effect": "inbound_reset", "inbound_tags": ["hy2"]},
            ]
        },
    )
    assert BRIDGE[locale] in result["disclosure"].casefold()
    assert "hy2" in result["disclosure"]
    assert result["writesBeforeConfirm"] == 0
