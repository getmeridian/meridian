"""Tests for the subscription page apply handlers.

Covers _handle_add_subscription_page and _handle_remove_subscription_page —
the production paths the system lab does not yet exercise.

Tests focus on behavior at the SSH boundary: which commands are issued in
which order, what nginx configuration is injected/removed, and how cluster
state transitions on success and failure.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from meridian.cluster import ClusterConfig, PanelConfig, SubscriptionPageConfig
from meridian.commands.apply import (
    _handle_add_subscription_page,
    _handle_remove_subscription_page,
)
from meridian.reconciler.diff import PlanAction, PlanActionKind


def _make_cluster(sub_page: SubscriptionPageConfig | None = None) -> ClusterConfig:
    return ClusterConfig(
        version=2,
        panel=PanelConfig(
            url="https://198.51.100.1/panel/secret",
            api_token="fake-tok",
            server_ip="198.51.100.1",
            ssh_user="root",
            ssh_port=22,
        ),
        subscription_page=sub_page or SubscriptionPageConfig(enabled=True, path=""),
    )


def _make_panel() -> object:
    from meridian.remnawave import MeridianPanel

    return MeridianPanel.__new__(MeridianPanel)


def _result(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestHandleAddSubscriptionPage:
    def test_aborts_when_panel_server_ip_missing(self) -> None:
        cluster = _make_cluster()
        cluster.panel.server_ip = ""
        action = PlanAction(kind=PlanActionKind.ADD_SUBSCRIPTION_PAGE, target="subscription-page")
        with pytest.raises(RuntimeError, match="server IP not set"):
            _handle_add_subscription_page(action, _make_panel(), cluster)

    def test_starts_stopped_container_when_service_already_in_compose(self) -> None:
        # Covers the disable-then-re-enable path: service exists in compose
        # but was stopped by a prior REMOVE_SUBSCRIPTION_PAGE. The handler
        # must `docker compose up -d` it so nginx has something to proxy to.
        cluster = _make_cluster(SubscriptionPageConfig(enabled=True, path="aaa111"))
        action = PlanAction(kind=PlanActionKind.ADD_SUBSCRIPTION_PAGE, target="subscription-page")

        conn = MagicMock()
        conn.put_text.return_value = _result(0)
        conn.run.side_effect = [
            _result(0),  # docker compose config | grep subscription → service present
            _result(0),  # docker compose up -d remnawave-subscription-page → idempotent
            _result(0),  # grep subscription path in nginx → already present
        ]

        with (
            patch("meridian.ssh.ServerConnection", return_value=conn),
            patch(
                "meridian.provision.remnawave_panel.configure_subscription_page",
                return_value=True,
            ),
            patch.object(ClusterConfig, "save"),
        ):
            _handle_add_subscription_page(action, _make_panel(), cluster)

        commands = [c.args[0] for c in conn.run.call_args_list]
        assert any("docker compose config" in c for c in commands)
        # Critical: we run `docker compose up -d` against the specific service
        # to restart it if it was stopped — not a full compose up.
        assert any("docker compose up -d remnawave-subscription-page" in c for c in commands)
        assert any("grep -q aaa111" in c for c in commands)
        assert not any("sed -i" in c for c in commands)

        assert cluster.subscription_page.enabled is True
        assert cluster.subscription_page.path == "aaa111"
        assert cluster.subscription_page.deployed is True

    def test_regenerates_compose_and_writes_env_when_service_missing(self) -> None:
        cluster = _make_cluster()
        action = PlanAction(kind=PlanActionKind.ADD_SUBSCRIPTION_PAGE, target="subscription-page")

        conn = MagicMock()
        conn.put_text.side_effect = [
            _result(0),  # docker-compose.yml
            _result(0),  # .env.subscription
        ]
        conn.run.side_effect = [
            _result(1),  # docker compose config | grep → service missing
            _result(0),  # docker compose up -d
            _result(1),  # grep nginx → not present, must inject
            _result(0),  # sed -i inject
            _result(0),  # nginx -t
            _result(0),  # systemctl reload nginx
        ]

        with (
            patch("meridian.ssh.ServerConnection", return_value=conn),
            patch(
                "meridian.provision.remnawave_panel.configure_subscription_page",
                return_value=True,
            ),
            patch(
                "meridian.provision.remnawave_panel._render_panel_compose",
                return_value="version: '3'\nservices: {}\n",
            ),
            patch(
                "meridian.provision.remnawave_panel._render_subscription_env",
                return_value="API_TOKEN=fake\n",
            ),
            patch.object(ClusterConfig, "save"),
        ):
            _handle_add_subscription_page(action, _make_panel(), cluster)

        commands = [c.args[0] for c in conn.run.call_args_list]
        writes = conn.put_text.call_args_list
        assert any(c.args[0].endswith("/docker-compose.yml") and c.kwargs["mode"] == "644" for c in writes)
        assert any(
            c.args[0].endswith("/.env.subscription") and c.kwargs["mode"] == "600" and c.kwargs["sensitive"] is True
            for c in writes
        )
        # Containers brought up
        assert any("docker compose up -d" in c for c in commands)
        # Nginx location injected and reloaded
        assert any("sed -i" in c and "Subscription Page" in c for c in commands)
        assert any("nginx -t" in c for c in commands)
        assert any("systemctl reload nginx" in c for c in commands)

        # Path got auto-generated (we left it empty in the cluster fixture)
        assert cluster.subscription_page.path
        assert cluster.subscription_page.enabled is True
        assert cluster.subscription_page.deployed is True

    def test_failed_compose_write_raises_and_does_not_persist_state(self) -> None:
        cluster = _make_cluster()
        action = PlanAction(kind=PlanActionKind.ADD_SUBSCRIPTION_PAGE, target="subscription-page")

        conn = MagicMock()
        conn.put_text.return_value = _result(1, stderr="disk full")
        conn.run.side_effect = [
            _result(1),  # service missing
        ]

        save = MagicMock()
        with (
            patch("meridian.ssh.ServerConnection", return_value=conn),
            patch(
                "meridian.provision.remnawave_panel._render_panel_compose",
                return_value="content",
            ),
            patch.object(ClusterConfig, "save", save),
        ):
            with pytest.raises(RuntimeError, match="docker-compose.yml"):
                _handle_add_subscription_page(action, _make_panel(), cluster)

        # cluster.save must NOT be called when we never got to the success path
        save.assert_not_called()
        assert cluster.subscription_page.deployed is not True

    def test_nginx_validation_failure_raises(self) -> None:
        cluster = _make_cluster(SubscriptionPageConfig(enabled=True, path="newpath"))
        action = PlanAction(kind=PlanActionKind.ADD_SUBSCRIPTION_PAGE, target="subscription-page")

        conn = MagicMock()
        conn.put_text.return_value = _result(0)
        conn.run.side_effect = [
            _result(0),  # service present in compose
            _result(0),  # docker compose up -d remnawave-subscription-page
            _result(1),  # nginx grep — location block not present yet
            _result(0),  # sed inject
            _result(1, stdout="syntax error"),  # nginx -t fails
        ]

        with (
            patch("meridian.ssh.ServerConnection", return_value=conn),
            patch(
                "meridian.provision.remnawave_panel.configure_subscription_page",
                return_value=True,
            ),
            patch.object(ClusterConfig, "save"),
        ):
            with pytest.raises(RuntimeError, match="nginx validation failed"):
                _handle_add_subscription_page(action, _make_panel(), cluster)


class TestHandleRemoveSubscriptionPage:
    def test_stops_container_and_cleans_nginx(self) -> None:
        cluster = _make_cluster(SubscriptionPageConfig(enabled=True, path="cleanme"))
        action = PlanAction(kind=PlanActionKind.REMOVE_SUBSCRIPTION_PAGE, target="subscription-page")

        conn = MagicMock()
        conn.run.side_effect = [
            _result(0),  # docker compose stop subscription-page
            _result(0),  # grep && sed -i to remove nginx block
            _result(0),  # nginx -t
            _result(0),  # systemctl reload
        ]

        with (
            patch("meridian.ssh.ServerConnection", return_value=conn),
            patch.object(ClusterConfig, "save"),
        ):
            _handle_remove_subscription_page(action, _make_panel(), cluster)

        commands = [c.args[0] for c in conn.run.call_args_list]
        assert any("docker compose stop remnawave-subscription-page" in c for c in commands)
        # Nginx block removal touches Subscription Page comment marker
        assert any("Subscription Page" in c or "cleanme" in c for c in commands)

        # State updated — disabled, deployed=False
        assert cluster.subscription_page.enabled is False
        assert cluster.subscription_page.deployed is False

    def test_raises_when_panel_server_ip_missing(self) -> None:
        cluster = _make_cluster()
        cluster.panel.server_ip = ""
        action = PlanAction(kind=PlanActionKind.REMOVE_SUBSCRIPTION_PAGE, target="subscription-page")
        with pytest.raises(RuntimeError, match="server IP not set"):
            _handle_remove_subscription_page(action, _make_panel(), cluster)

    def test_docker_stop_failure_raises(self) -> None:
        cluster = _make_cluster(SubscriptionPageConfig(enabled=True, path="cleanme"))
        action = PlanAction(kind=PlanActionKind.REMOVE_SUBSCRIPTION_PAGE, target="subscription-page")

        conn = MagicMock()
        conn.run.return_value = _result(1, stderr="container not found")

        with (
            patch("meridian.ssh.ServerConnection", return_value=conn),
            patch.object(ClusterConfig, "save"),
        ):
            with pytest.raises(RuntimeError, match="stop subscription page"):
                _handle_remove_subscription_page(action, _make_panel(), cluster)
