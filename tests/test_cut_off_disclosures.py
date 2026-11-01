"""Cut-off consent states each node's exact reconnect scope."""

import pytest

from meridian.commands.node_command_results import cut_off_disclosure
from meridian.control.command_models import CutOffNode, CutOffPlan
from meridian.i18n import set_locale

BRIDGE = {"en": "bridge", "ru": "мост", "fa": "پل", "zh": "桥"}


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_cli_discloses_every_inbound_and_bridge_for_each_core_restart(locale):
    set_locale(locale)
    try:
        text = cut_off_disclosure(
            CutOffPlan(
                nodes=[
                    CutOffNode(node_id="tcp-node", inbound_tags=["vless"], effect="core_restart"),
                    CutOffNode(node_id="udp-node", inbound_tags=["hy2"], effect="inbound_reset"),
                ]
            )
        )
    finally:
        set_locale("en")
    core = [line for line in text.splitlines() if "tcp-node" in line]
    inbound = [line for line in text.splitlines() if "udp-node" in line]
    assert len(core) == len(inbound) == 1
    assert BRIDGE[locale] in core[0].casefold()
    assert "hy2" in inbound[0]
    assert BRIDGE[locale] in inbound[0].casefold()


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_cli_localizes_retryable_core_preconditions(locale, monkeypatch):
    from io import StringIO

    from rich.console import Console

    from meridian.commands import node_command_results
    from meridian.control.command_models import NodeCommandOutcome
    from meridian.core.agent_protocol import ResourceError
    from tests.support.node_commands import command_record

    record = command_record("main", "failed", unit="unit:netcore")
    error = ResourceError(code="apply_pending", message="English precondition sentinel", retryable=True)
    record = record.model_copy(update={"result": record.result.model_copy(update={"error": error})})
    output = StringIO()
    monkeypatch.setattr(node_command_results, "err_console", Console(file=output, width=160))
    set_locale(locale)
    try:
        node_command_results.print_command_results(
            [NodeCommandOutcome(node_id="main", status="failed", commands=[record], errors=[error.message])]
        )
    finally:
        set_locale("en")
    assert error.message not in output.getvalue()
