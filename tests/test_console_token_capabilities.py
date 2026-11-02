"""The owner reviews effective permissions before issuing a narrow token."""

import pytest

from meridian.control.token_capabilities import permission
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("language", ["en", "ru", "fa", "zh"])
def test_console_reviews_native_permission_labels_and_issues_only_selected_actions(language: str) -> None:
    result = run_console("token-grants", language, data={"grants": ["people.read", "people.reset"]})
    assert result["writesAfterPreview"] == 0
    assert permission("people.reset").labels[language] in result["preview"]
    assert result["writes"] == 1
    assert result["bodies"][0]["capabilities"] == ["people.read", "people.reset"]
    assert result["bodies"][0]["expires_at"]
    assert "expires_in_days" not in result["bodies"][0]


def test_editing_a_reviewed_token_requires_a_new_preview() -> None:
    result = run_console("token-preview-edit")
    assert result["writesAfterPreview"] == result["writesAfterEdit"] == 0
    assert result["writes"] == 1
