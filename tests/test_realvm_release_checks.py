"""Negative cases for the release rehearsal; no network or provider credentials."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from meridian.ssh import CommandResult
from tests.realvm import release_checks as checks
from tests.realvm import upgrade_verifier as upgrade
from tests.realvm.upgrade_support import VerificationError


def test_wrong_source_release_refused_before_provider_access(tmp_path, monkeypatch) -> None:
    (tmp_path / "VERSION").write_text("4.0.0b17")
    monkeypatch.setattr(upgrade, "source_checkout", lambda: tmp_path)
    monkeypatch.setattr(upgrade, "agent_binary", lambda _: SimpleNamespace(version="test"))
    with pytest.raises(VerificationError, match="require REALVM_UPGRADE_FROM"):
        upgrade.preflight_upgrade(tmp_path, {"REALVM_RELEASE_CHECKS": "1", "REALVM_UPGRADE_FROM": "4.0.0b16"})


@pytest.mark.parametrize("fault", ["different-snapshot", "wrong-fleet", "old-epoch", "other-host", "redirected-path"])
def test_control_token_is_not_sent_to_an_unattested_restored_fleet(tmp_path, monkeypatch, fault) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "fleet-id").write_text("orchestrator-run")
    run = upgrade.UpgradeVerifier(
        tmp_path,
        tmp_path / "work",
        "198.51.100.10",
        {
            "MERIDIAN_HOME": str(home),
            "MERIDIAN_FLEET_ID": "orchestrator-run",
        },
    )
    snapshot = run.work / "before-handover.snapshot"
    snapshot.write_bytes(b"placeholder")
    record = SimpleNamespace(
        step="done",
        links_change=False,
        fleet_epoch=1 if fault == "old-epoch" else 2,
        snapshot=str(tmp_path / "some-other-snapshot" if fault == "different-snapshot" else snapshot),
    )
    host = "198.51.100.11" if fault == "other-host" else run.ip
    panel = SimpleNamespace(
        url=f"https://{host}/path",
        secret_path="other" if fault == "redirected-path" else "path",
        api_token="private-test-token",
        server_ip=run.ip,
    )
    cluster = SimpleNamespace(
        fleet_id="unrelated" if fault == "wrong-fleet" else "original", control_restore=record, panel=panel
    )
    monkeypatch.setattr(checks.ClusterConfig, "load", lambda _: cluster)
    monkeypatch.setattr(checks, "read_header", lambda _: SimpleNamespace(fleet_id="original", fleet_epoch=1))
    monkeypatch.setattr(checks.httpx, "Client", lambda **_: pytest.fail("A live client was opened"))
    with pytest.raises(VerificationError, match="cannot be tied"):
        checks.verify_release_changes(run)


def test_other_fleets_are_never_modified_even_with_a_valid_http_endpoint() -> None:
    run = Mock(ip="198.51.100.10")
    visited = []

    def handle(request):
        visited.append(request.url.path)
        return httpx.Response(200, json={"fleet_id": "other", "fleet_epoch": 4})

    with httpx.Client(base_url="https://example.test/path/", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(VerificationError, match="restored fleet and epoch"):
            checks.verify_presentation(run, client, "original", 4)
    assert visited == ["/path/api/health"]
    run.execute.assert_not_called()


@pytest.mark.parametrize("fault", ["failed-edit", "stale-accepted", "apply-clobbered"])
def test_bad_settings_edits_fail_the_rehearsal_and_restart_the_agent(fault) -> None:
    run = Mock(ip="198.51.100.10")
    state = {"agent": "active", "version": 8, "profile_title": "Old", "update_interval_hours": 2}

    def execute(_, args, **kwargs):
        if "systemctl stop " in args[-1]:
            state["agent"] = "inactive"
        elif "systemctl start " in args[-1]:
            state["agent"] = "active"
        return CommandResult(args=args, returncode=0, stdout=state["agent"])

    def apply(*_):
        assert state["agent"] == "active"
        state["profile_title"] = "Old"
        return {
            "actions": [{"resource_id": "service", "changed": False, "success": True, "status": "converged"}],
            "plan_hash": "plan",
            "generation": 1,
            "all_succeeded": True,
            "changed": False,
            "summary": "",
            "exit_code": 0,
            "counts": {"actions": 1, "succeeded": 1, "failed": 0, "skipped": 0},
        }

    def handle(request):
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"fleet_id": "original", "fleet_epoch": 2})
        if request.method == "PATCH":
            assert state["agent"] == "inactive"
            if fault == "failed-edit":
                raise httpx.ConnectError("private URL and token", request=request)
            body = json.loads(request.content)
            expected = body.pop("expected_version")
            if expected == state["version"]:
                state.update(body)
                state["version"] += 1
            elif fault != "stale-accepted":
                return httpx.Response(409, json={"error": {"code": "stale_presentation"}})
        return httpx.Response(200, json={k: v for k, v in state.items() if k != "agent"})

    run.execute.side_effect = execute
    run.json_command.side_effect = apply
    reasons = {
        "failed-edit": "reach the service",
        "stale-accepted": "expected HTTP 409",
        "apply-clobbered": "overwrote",
    }
    with httpx.Client(base_url="https://example.test/private/", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(VerificationError, match=reasons[fault]) as error:
            checks.verify_presentation(run, client, "original", 2)
    assert "private" not in str(error.value)
    assert state["agent"] == "active"
    assert any("systemctl start " in c.args[1][-1] for c in run.execute.call_args_list)


@pytest.mark.parametrize("fault", ["wrong-person", "script-only", "cacheable", "app-empty", "none"])
def test_only_the_visible_personal_field_that_delivers_an_app_profile_passes(fault) -> None:
    expected = "https://example.test/private?x=a&y=b"
    field = '<textarea readonly aria-labelledby="manual-setup-title">{}</textarea>'
    seen = []

    def handle(request):
        assert str(request.url) == expected
        assert "authorization" not in request.headers
        seen.append(request.headers.get("user-agent"))
        if "text/html" in request.headers["accept"]:
            markup = field.format("another-person" if fault == "wrong-person" else expected.replace("&", "&amp;"))
            if fault != "script-only":
                markup = f"<noscript>{markup}</noscript>"
            return httpx.Response(
                200,
                text=markup,
                headers={
                    "Content-Type": "text/html; charset=utf-8",
                    "Cache-Control": "no-cache" if fault == "cacheable" else "private, no-store",
                },
            )
        return httpx.Response(
            200,
            json=[] if fault == "app-empty" else [{"outbounds": [{"protocol": "vless"}]}],
            headers={"subscription-userinfo": "upload=0; download=0; total=10; expire=1"},
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        if fault == "none":
            checks.verify_nojs(client, {"user": expected})
            assert seen == ["Mozilla/5.0", "Happ/1.0"]
        else:
            with pytest.raises(VerificationError) as exc:
                checks.verify_nojs(client, {"user": expected})
            assert "private" not in str(exc.value)
            assert len(seen) == (2 if fault == "app-empty" else 1)
