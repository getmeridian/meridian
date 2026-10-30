"""Tests for client add/list/show/reissue/remove/enable/disable commands.

Clients are the access users declared in the saved topology; runtime user
state lives in Remnawave and is read through MeridianPanel.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.client import (
    run_add,
    run_disable,
    run_enable,
    run_list,
    run_reissue,
    run_remove,
    run_show,
)
from meridian.commands.client_handoff import print_handoff_links
from meridian.console import set_json_mode
from meridian.core.topology import AccessIntent
from meridian.handoff import HandoffLinks
from meridian.remnawave import RemnawaveError, User
from tests.support.topology import panel_mock, single_exit_intent


def _make_cluster(tmp_path: Path, *, users: list[str] | None = None) -> ClusterConfig:
    """Save a configured cluster whose topology declares the given access users (alice and bob by default)."""
    cluster = ClusterConfig(
        panel=PanelConfig(
            url="https://198.51.100.1/panel",
            api_token="test-jwt-token",
            server_ip="198.51.100.1",
            sub_path="pages",
        ),
        topology_intent=single_exit_intent(users=users or ["alice", "bob"]),
    )
    cluster.save(tmp_path / "cluster.yml")
    return cluster


def _make_user(name: str = "alice", status: str = "ACTIVE") -> User:
    return User(
        id="101",
        short_uuid="abc123",
        vless_uuid="550e8400-e29b-41d4-a716-446655440099",
        username=name,
        status=status,
        used_traffic_bytes=1024 * 1024 * 100,  # 100 MB
        created_at="2026-04-01T12:00:00Z",
    )


def _make_panel_mock() -> MagicMock:
    return panel_mock(get_user=_make_user(), list_users=[_make_user("alice"), _make_user("bob")], delete_user=True)


@contextmanager
def _cli(
    cluster: ClusterConfig,
    panel: MagicMock | None = None,
    *,
    json_mode: bool = False,
    runtime: MagicMock | None = None,
) -> Iterator[MagicMock]:
    """Serve the saved cluster and panel to a client command; yields the MeridianPanel class mock."""
    set_json_mode(json_mode)
    try:
        with ExitStack() as stack:
            stack.enter_context(patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster))
            panel_cls = stack.enter_context(
                patch("meridian.control_access.ServiceAccessPanel", return_value=panel or _make_panel_mock())
            )
            if runtime is not None:
                stack.enter_context(patch("meridian.setup.runtime.SetupRuntime", return_value=runtime))
                stack.enter_context(patch("meridian.servers.ServerRegistry"))
            yield panel_cls
    finally:
        set_json_mode(False)


def _json(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


class TestRunAdd:
    def test_v4_add_updates_intent_and_uses_compiled_access_driver(self, tmp_home: Path) -> None:
        cluster = _make_cluster(tmp_home, users=["default"])
        panel = _make_panel_mock()
        panel.get_user.side_effect = [None, _make_user("alice")]
        runtime = MagicMock()
        runtime.apply_intent.return_value = SimpleNamespace(all_succeeded=True, changed=True, failed=[])

        with (
            _cli(cluster, panel, runtime=runtime),
            patch(
                "meridian.commands.client.client_handoffs",
                return_value=[HandoffLinks("alice", subscription_url="https://198.51.100.1/pages/sub/abc123")],
            ) as publish_handoff,
        ):
            run_add(names=["alice"])

        updated = runtime.apply_intent.call_args.args[0]
        assert updated.access.users == ["default", "alice"]
        publish_handoff.assert_called_once_with(cluster, ["alice"])

    @pytest.mark.parametrize(
        ("observed", "status"),
        [(_make_user("alice"), "ok"), (None, "failed")],
        ids=["observed", "unobserved"],
    )
    def test_v4_add_save_failure_reports_remote_state_change(
        self, observed: User | None, status: str, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An observed client comes back as partial data; otherwise the error says remote state may have changed."""
        from meridian.commands._helpers import ReviewedApplyPersistenceError

        panel = _make_panel_mock()
        panel.get_user.side_effect = [None, observed]
        runtime = MagicMock()
        runtime.apply_intent.side_effect = ReviewedApplyPersistenceError("disk full")

        with (
            _cli(_make_cluster(tmp_home, users=["default"]), panel, json_mode=True, runtime=runtime),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_add(names=["alice"])

        payload = _json(capsys)
        assert exc_info.value.exit_code == 3
        assert payload["status"] == status
        if observed:
            assert payload["data"]["clients"][0]["username"] == "alice"
            assert payload["warnings"][0]["details"]["remote_state_changed"] is True
            assert "Remote state changed" in payload["warnings"][0]["message"]
        else:
            assert payload["errors"][0]["category"] == "system"
            assert "remote state may have changed" in payload["errors"][0]["message"]

    @pytest.mark.parametrize(
        ("configured", "names"),
        [(True, ["alice", "alice"]), (True, [""]), (False, ["alice"])],
        ids=["duplicate-in-batch", "empty-name", "no-cluster"],
    )
    def test_invalid_add_fails(self, configured: bool, names: list[str], tmp_home: Path) -> None:
        cluster = _make_cluster(tmp_home) if configured else ClusterConfig()
        with _cli(cluster), pytest.raises(typer.Exit):
            run_add(names=names)

    def test_add_keeps_a_typed_name_and_gives_the_panel_a_latin_username(self, tmp_home: Path) -> None:
        panel = _make_panel_mock()
        panel.get_user.side_effect = [None, _make_user("biba")]
        runtime = MagicMock()
        runtime.apply_intent.return_value = SimpleNamespace(all_succeeded=True, changed=True, failed=[])

        with (
            _cli(_make_cluster(tmp_home, users=["default"]), panel, runtime=runtime),
            patch("meridian.commands.client.client_handoffs", return_value=[]) as publish_handoff,
        ):
            run_add(names=["Биба"])

        access = runtime.apply_intent.call_args.args[0].access
        assert (access.users, access.display_names) == (["default", "biba"], {"biba": "Биба"})
        assert publish_handoff.call_args.args[1] == ["biba"]


class TestRunShow:
    def test_handoff_urls_render_as_plain_text(self, capsys: pytest.CaptureFixture[str]) -> None:
        subscription_url = "https://[2001:db8::1]/[red]subscription[/red]"
        page_url = "https://[2001:db8::2]/[bold]page[/bold]"

        with patch("meridian.urls.generate_qr_terminal", return_value=""):
            print_handoff_links(subscription_url, page_url=page_url)

        output = capsys.readouterr().err
        assert subscription_url in output
        assert page_url in output

    def test_show_never_prints_panel_admin_credentials(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        panel = _make_panel_mock()
        cluster = _make_cluster(tmp_home)
        cluster.panel.admin_user = "panel-admin"
        cluster.panel.admin_pass = "panel-password"

        with _cli(cluster, panel):
            run_show(name="alice")

        panel.get_user.assert_called_once_with("alice")
        output = capsys.readouterr().err
        assert "panel-admin" not in output
        assert "panel-password" not in output

    def test_show_nonexistent_client_fails(self, tmp_home: Path) -> None:
        panel = _make_panel_mock()
        panel.get_user.return_value = None

        with _cli(_make_cluster(tmp_home), panel), pytest.raises(typer.Exit):
            run_show(name="nonexistent")

    def test_show_json_hands_over_the_public_subscription_url(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with _cli(_make_cluster(tmp_home), json_mode=True):
            run_show(name="alice")

        payload = _json(capsys)
        assert payload["schema"] == "meridian.output/v1"
        assert payload["command"] == "client.show"
        assert payload["data"]["handoff"]["subscription_available"] is True
        assert payload["data"]["handoff"]["subscription_url"] == "https://198.51.100.1/pages/sub/abc123"
        assert "subscription_url" not in payload["data"]["client"]
        assert "test-jwt-token" not in json.dumps(payload)


class TestShowConnectionPage:
    def test_show_offers_the_subscription_as_the_page_without_publication(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with _cli(_make_cluster(tmp_home), json_mode=True):
            run_show(name="alice")
        handoff = _json(capsys)["data"]["handoff"]
        assert handoff["share_available"] is True
        assert handoff["share_url"] == handoff["subscription_url"]


class TestRunList:
    def test_list_renders_panel_username_as_plain_text(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        panel = _make_panel_mock()
        panel.list_users.return_value = [_make_user("[red]visible[/red]")]
        cluster = _make_cluster(tmp_home)
        assert cluster.topology_intent is not None
        cluster.topology_intent = cluster.topology_intent.model_copy(
            update={"access": AccessIntent.model_construct(users=["[red]visible[/red]"])}
        )

        with _cli(cluster, panel):
            run_list()

        assert "[red]visible[/red]" in capsys.readouterr().err

    @pytest.mark.parametrize("users", [[_make_user("alice"), _make_user("bob")], []], ids=["clients", "empty"])
    def test_list_reads_panel_users(self, users: list[User], tmp_home: Path) -> None:
        panel = _make_panel_mock()
        panel.list_users.return_value = users

        with _cli(_make_cluster(tmp_home), panel):
            run_list()

        panel.list_users.assert_called_once()

    def test_list_json_outputs_envelope(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        with _cli(_make_cluster(tmp_home), json_mode=True):
            run_list()

        payload = _json(capsys)
        assert payload["schema"] == "meridian.output/v1"
        assert payload["command"] == "client.list"
        assert payload["data"]["summary"]["clients"] == 2
        assert payload["data"]["clients"][0]["traffic_used_bytes"] == 104857600


class TestRunRemove:
    def test_last_client_cannot_be_removed(self, tmp_home: Path) -> None:
        with _cli(_make_cluster(tmp_home, users=["alice"])) as panel_cls, pytest.raises(typer.Exit) as exc_info:
            run_remove(name="alice", yes=True)

        assert exc_info.value.exit_code == 2
        panel_cls.assert_not_called()

    def test_remove_retires_the_user_through_the_topology_without_page_ssh(self, tmp_home: Path) -> None:
        cluster = _make_cluster(tmp_home)
        panel = _make_panel_mock()
        panel.get_user.return_value = _make_user("bob")
        runtime = MagicMock()
        runtime.apply_intent.return_value = SimpleNamespace(all_succeeded=True, failure_summary="")

        with (
            _cli(cluster, panel, runtime=runtime),
            patch("meridian.ssh.ServerConnection") as remove_page,
        ):
            run_remove(name="bob", yes=True)

        assert runtime.apply_intent.call_args.args[0].access.users == ["alice"]
        remove_page.assert_not_called()
        panel.delete_user.assert_not_called()  # retirement owns the delete

    def test_remove_finds_a_client_by_the_name_they_were_added_with(self, tmp_home: Path) -> None:
        cluster = _make_cluster(tmp_home, users=["alice", "biba"])
        cluster.topology_intent = cluster.topology_intent.model_copy(  # type: ignore[union-attr]
            update={"access": AccessIntent(users=["alice", "biba"], display_names={"biba": "Биба"})}
        )
        panel = _make_panel_mock()
        panel.get_user.return_value = _make_user("biba")
        runtime = MagicMock()
        runtime.apply_intent.return_value = SimpleNamespace(all_succeeded=True, failure_summary="")

        with _cli(cluster, panel, runtime=runtime), patch("meridian.ssh.ServerConnection"):
            run_remove(name="биба", yes=True)

        access = runtime.apply_intent.call_args.args[0].access
        assert (access.users, access.display_names) == (["alice"], {})

    def test_an_unknown_name_in_any_script_is_refused_before_the_panel(self, tmp_home: Path) -> None:
        with _cli(_make_cluster(tmp_home)) as panel_cls, pytest.raises(typer.Exit) as exc_info:
            run_disable(name="Призрак")

        assert exc_info.value.exit_code == 2
        panel_cls.assert_not_called()

    def test_unfinished_retirement_is_a_system_error(self, tmp_home: Path) -> None:
        runtime = MagicMock()
        runtime.apply_intent.return_value = SimpleNamespace(all_succeeded=False, failure_summary="retire x: down")

        with _cli(_make_cluster(tmp_home), runtime=runtime), pytest.raises(typer.Exit) as exc_info:
            run_remove(name="bob", yes=True)

        assert exc_info.value.exit_code == 3

    def test_remove_json_refusal_emits_error_envelope(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        panel = _make_panel_mock()
        panel.get_user.return_value = None

        with _cli(_make_cluster(tmp_home), panel, json_mode=True), pytest.raises(typer.Exit):
            run_remove(name="nonexistent", yes=True)

        payload = _json(capsys)
        assert payload["status"] == "failed"
        assert payload["command"] == "client.remove"

    def test_remove_json_requires_yes_without_prompting(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        panel = _make_panel_mock()

        with _cli(_make_cluster(tmp_home), panel, json_mode=True), pytest.raises(typer.Exit) as exc_info:
            run_remove(name="alice", yes=False)

        payload = _json(capsys)
        assert exc_info.value.exit_code == 2
        assert payload["status"] == "failed"
        assert "--yes" in payload["errors"][0]["hint"]
        panel.delete_user.assert_not_called()


_NEW_VLESS = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"


def _reissued(name: str = "alice") -> User:
    """What Remnawave returns after revoke: same id and settings, new short UUID and VLESS UUID."""
    user = _make_user(name)
    return User(**{**user.__dict__, "short_uuid": "new-short-uuid-1234", "vless_uuid": _NEW_VLESS})


@contextmanager
def _reissue_cli(cluster: ClusterConfig, panel: MagicMock, *, json_mode: bool = False) -> Iterator[MagicMock]:
    with _cli(cluster, panel, json_mode=json_mode), patch("meridian.commands.client.print_handoff_links") as links:
        yield links


class TestRunReissue:
    def test_revokes_by_numeric_id_and_hands_off_the_new_subscription_page(self, tmp_home: Path) -> None:
        cluster = _make_cluster(tmp_home)
        panel = _make_panel_mock()
        panel.revoke_user_subscription.return_value = _reissued()

        with _reissue_cli(cluster, panel) as links:
            run_reissue(name="alice", yes=True)

        panel.revoke_user_subscription.assert_called_once_with("101")
        links.assert_called_once_with(
            "https://198.51.100.1/pages/sub/new-short-uuid-1234",
            page_url="https://198.51.100.1/pages/sub/new-short-uuid-1234",
        )
        # Settings stay the operator's: nothing beside the revoke touches the user.
        panel.delete_user.assert_not_called()
        panel.disable_user.assert_not_called()
        panel.enable_user.assert_not_called()

    def test_json_envelope_reveals_the_new_links(self, tmp_home: Path, capsys: pytest.CaptureFixture[str]) -> None:
        panel = _make_panel_mock()
        panel.revoke_user_subscription.return_value = _reissued()

        with _reissue_cli(_make_cluster(tmp_home), panel, json_mode=True):
            run_reissue(name="alice", yes=True)

        payload = _json(capsys)
        assert (payload["command"], payload["status"], payload["exit_code"]) == ("client.reissue", "ok", 0)
        assert payload["summary"]["changed"] is True
        assert payload["data"]["client"] == {
            "username": "alice",
            "id": "101",
            "status": "active",
            "page_url": "https://198.51.100.1/pages/sub/new-short-uuid-1234",
            "subscription_url": "https://198.51.100.1/pages/sub/new-short-uuid-1234",
        }

    def test_finds_a_client_by_the_name_they_were_added_with(self, tmp_home: Path) -> None:
        cluster = _make_cluster(tmp_home, users=["alice", "biba"])
        cluster.topology_intent = cluster.topology_intent.model_copy(  # type: ignore[union-attr]
            update={"access": AccessIntent(users=["alice", "biba"], display_names={"biba": "Биба"})}
        )
        panel = _make_panel_mock()
        panel.get_user.return_value = _make_user("biba")
        panel.revoke_user_subscription.return_value = _reissued("biba")

        with _reissue_cli(cluster, panel) as links:
            run_reissue(name="биба", yes=True)

        panel.get_user.assert_called_once_with("biba")
        assert links.call_args.kwargs["page_url"] == links.call_args.args[0]

    @pytest.mark.parametrize(
        ("name", "users"),
        [("ghost", ["alice", "bob"]), ("default", ["alice"])],
        ids=["unknown", "service-user"],
    )
    def test_refuses_names_outside_the_access_intent_and_lists_clients(
        self, name: str, users: list[str], tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with (
            _cli(_make_cluster(tmp_home, users=users), json_mode=True) as panel_cls,
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_reissue(name=name, yes=True)

        payload = _json(capsys)
        assert exc_info.value.exit_code == 2
        assert payload["errors"][0]["hint"] == "Your clients: " + ", ".join(users)
        panel_cls.assert_not_called()

    def test_json_requires_yes_before_touching_anything(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with _cli(_make_cluster(tmp_home), json_mode=True) as panel_cls, pytest.raises(typer.Exit) as exc_info:
            run_reissue(name="alice", yes=False)

        assert exc_info.value.exit_code == 2
        assert "--yes" in _json(capsys)["errors"][0]["hint"]
        panel_cls.assert_not_called()

    def test_declining_the_prompt_changes_nothing(self, tmp_home: Path) -> None:
        panel = _make_panel_mock()

        with (
            _reissue_cli(_make_cluster(tmp_home), panel) as links,
            patch("meridian.commands.client.confirm", return_value=False) as ask,
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_reissue(name="alice")

        assert exc_info.value.exit_code == 1
        assert "alice" in ask.call_args.args[0]
        links.assert_not_called()
        panel.revoke_user_subscription.assert_not_called()

    def test_reissue_needs_only_the_control_service(self, tmp_home: Path) -> None:
        panel = _make_panel_mock()
        panel.revoke_user_subscription.return_value = _reissued()
        with _reissue_cli(_make_cluster(tmp_home), panel), patch("meridian.ssh.ServerConnection") as ssh:
            run_reissue(name="alice", yes=True)
        ssh.assert_not_called()

    def test_service_reissue_failure_returns_a_plain_error(
        self, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        panel = _make_panel_mock()
        panel.revoke_user_subscription.side_effect = RemnawaveError("Service unavailable")
        with _reissue_cli(_make_cluster(tmp_home), panel, json_mode=True) as links, pytest.raises(typer.Exit):
            run_reissue(name="alice", yes=True)
        assert _json(capsys)["errors"][0]["message"].startswith("Could not reissue client")
        links.assert_not_called()


@pytest.mark.parametrize("command", ["add", "enable", "disable"])
def test_panel_lookup_error_emits_typed_json_failure(
    command: str, tmp_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    panel = _make_panel_mock()
    panel.get_user.side_effect = RemnawaveError("Panel lookup unavailable")

    with _cli(_make_cluster(tmp_home), panel, json_mode=True), pytest.raises(typer.Exit) as exc_info:
        if command == "add":
            run_add(names=["carol"])
        elif command == "enable":
            run_enable(name="alice")
        else:
            run_disable(name="alice")

    payload = _json(capsys)
    assert exc_info.value.exit_code == 3
    assert payload["command"] == f"client.{command}"
    assert payload["status"] == "failed"
    assert payload["errors"][0]["category"] == "system"


_TOGGLES = [
    pytest.param("enable", run_enable, "DISABLED", "active", id="enable"),
    pytest.param("disable", run_disable, "ACTIVE", "disabled", id="disable"),
]


@pytest.mark.parametrize(("action", "run", "before", "after"), _TOGGLES)
class TestToggleClient:
    def test_toggles_the_existing_client(self, action, run, before, after, tmp_home: Path) -> None:
        panel = _make_panel_mock()
        panel.get_user.return_value = _make_user("alice", status=before)

        with _cli(_make_cluster(tmp_home), panel):
            run(name="alice")

        panel.get_user.assert_called_once_with("alice")
        getattr(panel, f"{action}_user").assert_called_once_with("101")

    @pytest.mark.parametrize("json_mode", [False, True], ids=["text", "json"])
    def test_nonexistent_client_fails(
        self, action, run, before, after, json_mode: bool, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        panel = _make_panel_mock()
        panel.get_user.return_value = None

        with _cli(_make_cluster(tmp_home), panel, json_mode=json_mode), pytest.raises(typer.Exit):
            run(name="nonexistent")

        getattr(panel, f"{action}_user").assert_not_called()
        if json_mode:
            payload = _json(capsys)
            assert payload["status"] == "failed"
            assert payload["command"] == f"client.{action}"

    def test_api_error_fails(self, action, run, before, after, tmp_home: Path) -> None:
        panel = _make_panel_mock()
        panel.get_user.return_value = _make_user("alice", status=before)
        getattr(panel, f"{action}_user").side_effect = RemnawaveError("Panel unreachable")

        with _cli(_make_cluster(tmp_home), panel), pytest.raises(typer.Exit):
            run(name="alice")

    def test_json_outputs_envelope(
        self, action, run, before, after, tmp_home: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        panel = _make_panel_mock()
        panel.get_user.return_value = _make_user("alice", status=before)

        with _cli(_make_cluster(tmp_home), panel, json_mode=True):
            run(name="alice")

        payload = _json(capsys)
        assert payload["schema"] == "meridian.output/v1"
        assert payload["command"] == f"client.{action}"
        assert payload["summary"]["changed"] is True
        assert payload["data"]["client"]["username"] == "alice"
        assert payload["data"]["client"]["status"] == after


class TestRunDisable:
    def test_v4_disable_lasts_until_enabled_not_until_next_apply(self, tmp_home: Path) -> None:
        panel = _make_panel_mock()

        with _cli(_make_cluster(tmp_home, users=["alice"]), panel), patch("meridian.commands.client.warn") as warning:
            run_disable(name="alice")

        panel.disable_user.assert_called_once()
        # Apply leaves a client's status to the operator, so the old "until the next apply" warning is gone.
        warning.assert_not_called()

    def test_v4_disable_refuses_a_person_missing_from_the_service(self, tmp_home: Path) -> None:
        panel = _make_panel_mock()
        panel.get_user.return_value = None
        with (
            _cli(_make_cluster(tmp_home, users=["default"]), panel),
            pytest.raises(typer.Exit) as exc_info,
        ):
            run_disable(name="alice")

        assert exc_info.value.exit_code == 2
        panel.disable_user.assert_not_called()
