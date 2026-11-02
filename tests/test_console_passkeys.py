"""The console offers local passkeys with password fallback and clear management."""

import pytest

from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_passkey_sign_in_converts_browser_bytes_and_sends_only_its_proof(locale: str) -> None:
    result = run_console("passkey-login", locale)
    assert result["offered"] and result["signedIn"]
    assert result["browser"] == [
        {"kind": "get", "binaryChallenge": True, "binaryCredential": True, "verification": "required"}
    ]
    request = result["requests"][-1]
    assert request["path"] == "auth/passkeys/login/finish"
    assert request["body"]["credential"]["response"]["signature"] == "CgsM"
    assert "password" not in request["body"]
    assert all(item["headers"]["X-Meridian-Request"] == "1" for item in result["requests"])


@pytest.mark.parametrize("change", [{"noDomain": True}, {"unsupported": True}])
def test_unavailable_passkeys_leave_password_sign_in(change: dict) -> None:
    result = run_console("passkey-login", data=change)
    assert not result["offered"]
    assert "Password" in result["initial"] and "Sign in" in result["initial"]
    assert result["requests"] == []


def test_a_cancelled_authenticator_stays_on_the_sign_in_screen() -> None:
    result = run_console("passkey-login", data={"cancel": True})
    assert not result.get("signedIn")
    assert "cancel" in result["after"].lower()
    assert len(result["requests"]) == 1


def test_passkeys_confirm_identity_without_a_password() -> None:
    result = run_console("passkey-confirm")
    assert result["confirmed"]
    assert [item["path"] for item in result["requests"]] == [
        "auth/passkeys/confirm/begin",
        "auth/passkeys/confirm/finish",
    ]


@pytest.mark.parametrize("mode", ["passkey-login", "passkey-confirm"])
@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_empty_browser_owner_handles_are_sent_as_null(mode: str, locale: str) -> None:
    result = run_console(mode, locale, data={"emptyHandle": True})
    assert result.get("signedIn") or result.get("confirmed")
    assert result["requests"][-1]["body"]["credential"]["response"]["userHandle"] is None


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_settings_adds_a_named_passkey_with_user_verification_and_no_attestation(locale: str) -> None:
    result = run_console("passkey-settings", locale, data={"name": "Travel key"})
    assert result["browser"] == [
        {
            "kind": "create",
            "binaryChallenge": True,
            "binaryUser": True,
            "verification": "required",
            "attestation": "none",
        }
    ]
    issued = next(item for item in result["requests"] if item["path"].endswith("/register/finish"))
    assert issued["body"]["name"] == "Travel key"
    assert set(issued["body"]["credential"]["response"]) == {"clientDataJSON", "attestationObject"}
    assert not result["dialogOpen"]


@pytest.mark.parametrize("action", ["rename", "remove"])
def test_settings_manages_passkeys_through_the_owner_routes(action: str) -> None:
    result = run_console("passkey-settings", data={"action": action, "name": "Travel key"})
    writes = [item for item in result["requests"] if item["method"] in ("PATCH", "DELETE")]
    assert len(writes) == 1 and writes[0]["path"].startswith("auth/passkeys/")
    assert writes[0]["body"] == ({"name": "Travel key"} if action == "rename" else None)
    assert not result["dialogOpen"]
