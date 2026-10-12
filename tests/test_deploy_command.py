"""`meridian deploy` drives the same reviewed topology runtime as `meridian setup`."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import click
from typer.testing import CliRunner

import meridian.cli as cli
from meridian.cli import app
from meridian.console import set_json_mode, set_quiet_mode
from meridian.handoff import HandoffLinks

runner = CliRunner()


def _invoke(args: list[str]):
    try:
        return runner.invoke(app, args)
    finally:
        set_json_mode(False)
        set_quiet_mode(False)


def test_dry_run_compiles_a_single_server_topology_without_touching_state(tmp_home: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    monkeypatch.setattr("meridian.commands.deploy.SERVER_PROFILES_FILE", tmp_home / "servers.json")

    result = _invoke(["deploy", "198.51.100.10", "--domain", "vpn.example.com", "--dry-run", "--json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["mode"] == "first_deploy"
    assert data["ports"]["reality_port"] > 0 and data["ports"]["xhttp_port"] > 0
    assert data["xhttp_path"] and data["ws_path"]
    assert not (tmp_home / "cluster.yml").exists()
    assert not (tmp_home / "servers.json").exists()


def test_deploy_applies_the_intent_and_hands_off_a_page(tmp_home: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    monkeypatch.setattr("meridian.commands.deploy.SERVER_PROFILES_FILE", tmp_home / "servers.json")
    conn = MagicMock(port=22)
    resolved = SimpleNamespace(ip="198.51.100.10", user="root", port=22, machine_id="", conn=conn)
    runtime = MagicMock()
    runtime.apply_intent.return_value = SimpleNamespace(all_succeeded=True, failed=[], plan_hash="h" * 64)
    runtime.publish_handoff.return_value = [
        HandoffLinks(
            "alice",
            subscription_url="https://198.51.100.10/pages/sub/s1",
            page_url="https://198.51.100.10/pages/u1/",
        )
    ]

    with (
        patch("meridian.commands.deploy.resolve_server", return_value=resolved),
        patch("meridian.commands.deploy.ensure_server_connection", return_value=resolved),
        patch("meridian.commands.deploy.check_ports") as check_ports,
        patch("meridian.commands.deploy._reachable_reality_targets", return_value=["www.google.com"]),
        patch("meridian.commands.deploy._verify_traffic", return_value=("ok", "exit IP 198.51.100.10")),
        patch("meridian.setup.runtime.SetupRuntime", return_value=runtime),
    ):
        result = _invoke(["deploy", "198.51.100.10", "--client-name", "alice", "--no-geo-block", "--yes", "--json"])

    assert result.exit_code == 0, result.output
    check_ports.assert_called_once()
    intent = runtime.apply_intent.call_args.args[0]
    assert intent.access.users == ["alice"]
    assert intent.exits[0].geo_block is False and intent.harden is True
    runtime.verify.assert_called_once()
    runtime.publish_handoff.assert_called_once_with(["alice"])
    data = json.loads(result.stdout)["data"]
    assert data["mode"] == "first_deploy"
    assert data["sni"] == "www.google.com"
    # Machine output redacts capability URLs by policy; humans get them on stderr.
    assert data["connection_page_url"].startswith("https://198.51.100.10/")


def test_hysteria2_without_a_domain_is_refused_before_ssh(tmp_home: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)

    with patch("meridian.commands.deploy.resolve_server") as resolve_server:
        result = _invoke(["deploy", "198.51.100.10", "--hysteria2", "--dry-run"])

    assert result.exit_code == 2
    assert "Hysteria2 needs a domain" in click.unstyle(result.output)
    resolve_server.assert_not_called()


def _fake_runtime() -> MagicMock:
    runtime = MagicMock()
    runtime.apply_intent.return_value = SimpleNamespace(all_succeeded=True, failed=[], plan_hash="h" * 64)
    runtime.publish_handoff.return_value = [HandoffLinks("default", subscription_url="https://198.51.100.10/p/sub/s")]
    return runtime


def _deploy_with(runtime: MagicMock, traffic, targets=("www.google.com", "dl.google.com"), args=()):
    resolved = SimpleNamespace(ip="198.51.100.10", user="root", port=22, machine_id="", conn=MagicMock(port=22))
    with (
        patch("meridian.commands.deploy.resolve_server", return_value=resolved),
        patch("meridian.commands.deploy.ensure_server_connection", return_value=resolved),
        patch("meridian.commands.deploy.check_ports"),
        patch("meridian.commands.deploy._reachable_reality_targets", return_value=list(targets)),
        patch("meridian.commands.deploy._verify_traffic", side_effect=traffic),
        patch("meridian.setup.runtime.SetupRuntime", return_value=runtime) as runtime_cls,
    ):
        result = _invoke(["deploy", "198.51.100.10", "--yes", *args])
    runtime.constructed_with = runtime_cls.call_args.kwargs if runtime_cls.call_args else {}
    return result


def test_dead_camouflage_target_falls_back_to_the_next_candidate(tmp_home: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    monkeypatch.setattr("meridian.commands.deploy.SERVER_PROFILES_FILE", tmp_home / "servers.json")
    runtime = _fake_runtime()

    result = _deploy_with(runtime, [("failed", "connection reset"), ("ok", "exit IP 198.51.100.10")])

    assert result.exit_code == 0, result.output
    snis = [call.args[0].exits[0].paths[0].reality_sni for call in runtime.apply_intent.call_args_list]
    assert snis == ["www.google.com", "dl.google.com"]


def test_explicit_sni_is_never_swapped_and_dead_traffic_fails(tmp_home: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    monkeypatch.setattr("meridian.commands.deploy.SERVER_PROFILES_FILE", tmp_home / "servers.json")
    runtime = _fake_runtime()

    result = _deploy_with(runtime, [("failed", "connection reset")], args=("--sni", "www.example.com"))

    assert result.exit_code == 3
    assert "no traffic passes" in click.unstyle(result.output)
    assert runtime.apply_intent.call_count == 1
    runtime.publish_handoff.assert_not_called()


def test_egress_mismatch_and_inconclusive_evidence_do_not_fail_a_deploy(tmp_home: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    monkeypatch.setattr("meridian.commands.deploy.SERVER_PROFILES_FILE", tmp_home / "servers.json")

    for outcome in (("egress_mismatch", "exit IP 203.0.113.1 does not match"), ("inconclusive", "no observer")):
        result = _deploy_with(_fake_runtime(), [outcome])
        assert result.exit_code == 0, result.output


def test_probe_keeps_candidate_order_and_drops_unreachable_targets() -> None:
    from meridian.commands.deploy import _reachable_reality_targets
    from tests.support.mock_connection import MockConnection

    conn = MockConnection()
    conn.when("https://www.google.com/", rc=28)  # timeout
    conn.when("https://dl.google.com/", stdout="2")
    conn.when("https://www.cloudflare.com/", stdout="1.1")  # no HTTP/2

    assert _reachable_reality_targets(conn) == ["dl.google.com"]


def test_traffic_check_waits_for_node_warm_up_before_declaring_failure(monkeypatch) -> None:
    from meridian.commands import deploy

    outcomes = iter([("failed", "broken pipe"), ("failed", "broken pipe"), ("ok", "exit IP 198.51.100.10")])
    monkeypatch.setattr(deploy, "_traffic_once", lambda *_args: next(outcomes))
    monkeypatch.setattr(deploy.time, "sleep", lambda _seconds: None)

    assert deploy._verify_traffic("198.51.100.10", "default") == ("ok", "exit IP 198.51.100.10")


def test_cancel_file_present_before_start_reports_cancelled_without_ssh(tmp_home: Path, monkeypatch) -> None:
    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    monkeypatch.setattr("meridian.commands.deploy.SERVER_PROFILES_FILE", tmp_home / "servers.json")
    cancel_file = tmp_home / "cancel"
    cancel_file.touch()

    with patch("meridian.commands.deploy.resolve_server") as resolve_server:
        result = _invoke(["deploy", "198.51.100.10", "--yes", "--json", "--cancel-file", str(cancel_file)])

    assert result.exit_code == 5, result.output
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "cancelled"
    assert envelope["data"]["phase"] == "preflight"
    assert envelope["data"]["remote_changed"] is False
    assert envelope["errors"][0]["category"] == "cancelled"
    resolve_server.assert_not_called()


def test_cancel_during_apply_stops_before_verification_and_handoff(tmp_home: Path, monkeypatch) -> None:
    from meridian.core.cancellation import OperationCancellation, OperationCancelledError

    monkeypatch.setattr(cli, "DISABLE_UPDATE_CHECK", True)
    monkeypatch.setattr("meridian.commands.deploy.SERVER_PROFILES_FILE", tmp_home / "servers.json")
    runtime = _fake_runtime()
    runtime.apply_intent.side_effect = OperationCancelledError(
        OperationCancellation(
            phase="apply", message="Apply stopped after 4 of 12 resource actions.", remote_changed=True
        )
    )

    result = _deploy_with(runtime, [("ok", "unused")], args=("--json", "--cancel-file", str(tmp_home / "cancel")))

    assert result.exit_code == 5, result.output
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "cancelled"
    assert envelope["summary"]["changed"] is True
    assert envelope["data"]["message"] == "Apply stopped after 4 of 12 resource actions."
    runtime.verify.assert_not_called()
    runtime.publish_handoff.assert_not_called()
    assert runtime.constructed_with["cancellation"].path == tmp_home / "cancel"
