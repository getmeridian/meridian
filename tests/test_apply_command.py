"""`meridian apply` and `meridian plan` only drive the reviewed topology runtime."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest
import typer

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.apply import run as apply_run
from meridian.commands.plan import run as plan_run
from meridian.console import set_json_mode
from meridian.core.errors import LocalStateCorruptedError
from meridian.setup.single_server import single_server_intent


def _topology_cluster() -> ClusterConfig:
    return ClusterConfig(topology_intent=single_server_intent(None, server_ref="srv-a"))


def test_apply_routes_through_the_checkpointed_runtime() -> None:
    cluster = _topology_cluster()
    with (
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.apply.run_v4_apply") as run_v4,
    ):
        apply_run(yes=True)

    run_v4.assert_called_once()
    assert run_v4.call_args.args[0] is cluster


def test_plan_routes_through_the_compiler_graph() -> None:
    cluster = _topology_cluster()
    with (
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.plan.run_v4_plan") as run_v4,
    ):
        plan_run()

    run_v4.assert_called_once()


def test_plan_without_a_fleet_exits_one_so_ci_never_reads_it_as_pending(tmp_home) -> None:
    with pytest.raises(typer.Exit) as exc_info:
        plan_run()

    assert exc_info.value.exit_code == 1


def test_apply_without_a_fleet_is_a_user_error(tmp_home) -> None:
    with pytest.raises(typer.Exit) as exc_info:
        apply_run(yes=True)

    assert exc_info.value.exit_code == 2


def test_pre_topology_cluster_is_refused_with_rebuild_guidance(tmp_home, capsys) -> None:
    cluster = ClusterConfig(panel=PanelConfig(url="https://198.51.100.1/p", api_token="t", server_ip="198.51.100.1"))
    with (
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        pytest.raises(typer.Exit),
    ):
        apply_run(yes=True)

    assert "development build" in capsys.readouterr().err


@pytest.mark.parametrize(("command", "runner", "code"), [("apply", apply_run, 2), ("plan", plan_run, 1)])
def test_malformed_cluster_emits_one_typed_envelope(command, runner, code) -> None:
    buf = io.StringIO()
    error = LocalStateCorruptedError("Cannot safely load cluster.yml", hint="Restore a backup.")

    try:
        with (
            patch.object(ClusterConfig, "load", side_effect=error),
            redirect_stdout(buf),
            pytest.raises(typer.Exit) as exc_info,
        ):
            runner(json_output=True)
    finally:
        set_json_mode(False)

    assert exc_info.value.exit_code == code
    payload, end = json.JSONDecoder().raw_decode(buf.getvalue())
    assert not buf.getvalue()[end:].strip()
    assert payload["command"] == command
    assert payload["status"] == "failed"
    assert payload["errors"][0]["category"] == "user"
