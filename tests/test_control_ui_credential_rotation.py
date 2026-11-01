"""The console distinguishes connection-key rotation from a replacement subscription URL."""

import pytest

from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_console_rotation_confirms_once_preserves_drafts_and_sends_generation(locale):
    result = run_console(
        "person-credential-rotation", locale, {"initialStatus": "active", "user": {"credential_generation": 1}}
    )
    assert result["found"]
    assert result["beforeConfirm"] == []
    assert result["writes"] == [{"expected_generation": 1, "cut_off": False}]
    assert result["draft"] == "Private draft"
    assert not result["disabledAfter"]
    assert "person.rotate" not in result["text"]


def test_declared_people_get_the_rotation_command_instead_of_a_console_write():
    result = run_console(
        "person-credential-rotation", data={"share": {"declared": True}, "user": {"credential_generation": 1}}
    )
    assert not result["found"]
    assert "meridian client rotate-credentials sample" in result["text"]
    assert result["writes"] == []
