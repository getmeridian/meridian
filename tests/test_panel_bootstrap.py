"""Control-plane credential bootstrap against an already-initialized panel."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from meridian.cluster import ClusterConfig
from meridian.core.errors import PanelSetupError
from meridian.panel_bootstrap import ensure_control_plane_access
from meridian.remnawave import RemnawaveAuthError


def test_panel_with_an_unknown_admin_fails_once_with_recovery_guidance() -> None:
    resolved = SimpleNamespace(ip="198.51.100.10", user="root", conn=SimpleNamespace(port=22))
    refused = RemnawaveAuthError("Panel registration failed (403)", category="user")
    with (
        patch("meridian.panel_bootstrap.check_panel_api_ready", return_value=True),
        patch("meridian.panel_bootstrap.MeridianPanel.register_admin", side_effect=refused),
        patch("meridian.panel_bootstrap.MeridianPanel.login", side_effect=refused),
        pytest.raises(PanelSetupError) as exc_info,
    ):
        ensure_control_plane_access(
            resolved=resolved,
            cluster=ClusterConfig(),
            domain="",
            secret_path="a" * 24,
            info_page_path="b" * 16,
            version="4.0.0",
        )

    assert "administrator Meridian doesn't know" in str(exc_info.value)
    assert exc_info.value.category == "user"
    assert "meridian uninstall 198.51.100.10" in exc_info.value.hint
