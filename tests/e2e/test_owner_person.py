"""Owner choices travel through the connection page to the same link's VPN app."""

import base64
import re

import httpx
import pytest
from playwright.sync_api import Page, expect

from meridian.control.service import ControlService
from tests.e2e.helpers import add_person, connected, connections, document, editor, person, share_url


def test_console_invitee_receives_the_reviewed_expiry_and_allowance_in_page_and_app(
    owner: Page, visitor: Page, app: httpx.Client
) -> None:
    add_person(owner)
    url = share_url(owner)
    connected(visitor, url, "E2E_visitor")
    expect(visitor.locator("#account-panel")).to_contain_text(re.compile(r"2027-02-15T12:00:00(?:\.000)?Z UTC"))
    expect(visitor.locator(".account-metric").filter(has_text="Traffic this period")).to_contain_text("0 B / 12 GiB")
    connections(app, url)
    reply = app.get(url)
    assert f"total={12 * 1024**3}" in reply.headers["subscription-userinfo"]
    assert "expire=1802692800" in reply.headers["subscription-userinfo"]


def test_disable_and_toast_undo_reach_an_already_shared_page_and_app(
    owner: Page, visitor: Page, app: httpx.Client
) -> None:
    person(owner)
    url = share_url(owner)
    connected(visitor, url, "Luma")
    before = connections(app, url)
    owner.get_by_role("button", name="Disable", exact=True).click()
    expect(owner.get_by_role("button", name="Enable", exact=True)).to_be_enabled()
    visitor.get_by_role("button", name="Refresh", exact=True).click()
    expect(visitor.locator(".account-status")).to_have_text("Disabled")
    expect(visitor.locator("#import-status")).to_contain_text("disabled")
    stopped = document(app, url)
    assert stopped != before and "disabled" in stopped.lower()
    owner.locator(".toast").get_by_role("button", name="Undo", exact=True).click()
    expect(owner.get_by_role("button", name="Disable", exact=True)).to_be_enabled()
    visitor.get_by_role("button", name="Refresh", exact=True).click()
    expect(visitor.locator(".account-status")).to_have_text("Active")
    assert connections(app, url) == before


def test_extended_access_and_inline_allowance_survive_reload_but_reset_waits_for_confirmation(
    owner: Page, visitor: Page, app: httpx.Client, service: ControlService
) -> None:
    person(owner)
    url = share_url(owner)
    connected(visitor, url, "Luma")
    owner.locator('[data-person-action="extend"]').click()
    owner.get_by_role("button", name="+1 month", exact=True).click()
    expiry = owner.locator('[data-person-field="expiry"] form')
    expect(expiry.locator('input[type="date"]')).to_have_value("2027-03-14")
    expiry.get_by_role("button", name="Save", exact=True).click()
    expect(expiry).not_to_be_visible()
    traffic = editor(owner, "traffic")
    traffic.get_by_label("Traffic limit", exact=True).fill("125")
    traffic.get_by_role("button", name="Save", exact=True).click()
    expect(traffic).not_to_be_visible()
    owner.reload()
    expect(owner.locator('[data-person-field="traffic"]')).to_contain_text("125 GiB")
    visitor.reload()
    expect(visitor.locator("#account-panel")).to_have_attribute("aria-busy", "false")
    expect(visitor.locator("#account-panel")).to_contain_text(re.compile(r"2027-03-14T12:00:00(?:\.000)?Z UTC"))
    expect(visitor.locator(".account-metric").filter(has_text="Traffic this period")).to_contain_text("125 GiB")
    assert f"total={125 * 1024**3}" in app.get(url).headers["subscription-userinfo"]
    owner.get_by_role("tab", name="Usage", exact=True).click()
    before = service.get_user("Luma")
    assert before is not None and before.used_traffic_bytes > 0
    owner.get_by_role("button", name="Reset traffic", exact=True).click()
    dialog = owner.get_by_role("dialog")
    expect(dialog).to_contain_text("Reset")
    assert service.get_user("Luma") == before
    dialog.get_by_role("button", name="Cancel", exact=True).click()
    assert service.get_user("Luma") == before
    owner.get_by_role("button", name="Reset traffic", exact=True).click()
    owner.get_by_role("dialog").get_by_role("button", name="Reset traffic", exact=True).click()
    expect(owner.locator(".toast").filter(has_text="Reset")).to_be_visible()
    after = service.get_user("Luma")
    assert after is not None and after.used_traffic_bytes == 0
    assert after.lifetime_traffic_bytes == before.lifetime_traffic_bytes
    visitor.reload()
    expect(visitor.locator(".account-metric").filter(has_text="Traffic this period")).to_contain_text("0 B / 125 GiB")


@pytest.mark.parametrize(
    ("declared", "cut_off"),
    [(False, False), (False, True), (True, False)],
    ids=["let-sessions-end", "end-open-sessions", "fleet-file-person"],
)
def test_new_link_erases_the_old_identity_or_explains_fleet_file_ownership(
    owner: Page, visitor: Page, app: httpx.Client, base_url: str, declared: bool, cut_off: bool, browser_faults
) -> None:
    if declared:
        person(owner, "Tavi")
        badge = owner.locator("[data-person-declared]")
        expect(badge).to_be_visible()
        badge.get_by_role("button").click()
        expect(badge.get_by_role("status")).to_contain_text("fleet")
        owner.keyboard.press("Escape")
        owner.get_by_role("tab", name="Keys & removal", exact=True).click()
        expect(owner.get_by_text("meridian client reissue Tavi", exact=True)).to_be_visible()
        expect(owner.get_by_role("button", name="Issue a new link", exact=True)).to_have_count(0)
        return
    add_person(owner)
    old_url = share_url(owner)
    connected(visitor, old_url, "E2E_visitor")
    owner.get_by_role("tab", name="Keys & removal", exact=True).click()
    owner.get_by_role("button", name="Issue a new link", exact=True).click()
    dialog = owner.get_by_role("dialog")
    if cut_off:
        dialog.get_by_role("checkbox", name="End open sessions now", exact=True).check()
    dialog.get_by_role("button", name="Issue a new link", exact=True).click()
    expect(owner.locator(".toast").filter(has_text="Issued a new link")).to_be_visible()
    if cut_off:
        owner.get_by_role("dialog").locator(".dialog-footer").get_by_role("button", name="Close", exact=True).click()
    new_url = share_url(owner)
    assert new_url != old_url
    unknown = base_url + "/sub/UnknownTokenThatWasNeverIssued"
    for headers in ({"Accept": "text/html", "User-Agent": "Mozilla/5.0"}, {"User-Agent": "Happ/1.0"}):
        old, missing = app.get(old_url, headers=headers), app.get(unknown, headers=headers)
        assert (old.status_code, old.content, {k: v for k, v in old.headers.items() if k != "date"}) == (
            missing.status_code,
            missing.content,
            {k: v for k, v in missing.headers.items() if k != "date"},
        )
    browser_faults.expect(r"console: .*?/sub/.* 404")
    old_reply = visitor.goto(old_url)
    assert old_reply is not None and old_reply.status == 404
    old_body = visitor.locator("body").inner_text()
    browser_faults.expect(r"console: .*?/sub/UnknownToken.* 404")
    visitor.goto(unknown)
    expect(visitor.locator("body")).to_have_text(old_body)
    connected(visitor, new_url, "E2E_visitor")
    connections(app, new_url)


def test_published_notice_substitutes_the_person_across_page_and_app_then_disappears(
    owner: Page, visitor: Page, app: httpx.Client
) -> None:
    person(owner)
    url = share_url(owner)
    connected(visitor, url, "Luma")
    owner.get_by_role("navigation").get_by_role("link", name="Settings", exact=True).click()
    owner.get_by_role("tab", name="Notice", exact=True).click()
    owner.locator('[data-notice="text"]').fill("Hello {name}. Maintenance is finished.")
    owner.locator('[data-notice="person"]').fill("Luma")
    owner.get_by_role("button", name="Save", exact=True).click()
    dialog = owner.get_by_role("dialog")
    expect(dialog).to_contain_text("Hello Luma. Maintenance is finished.")
    dialog.get_by_role("button", name="Save", exact=True).click()
    expect(dialog).not_to_be_visible()
    visitor.get_by_role("button", name="Refresh", exact=True).click()
    expect(visitor.locator("#owner-notice")).to_contain_text("Hello Luma. Maintenance is finished.")
    announcement = app.get(url).headers["announce"]
    assert base64.b64decode(announcement.removeprefix("base64:")).decode() == "Hello Luma. Maintenance is finished."
    owner.get_by_role("button", name="Clear notice", exact=True).click()
    owner.get_by_role("dialog").get_by_role("button", name="Clear notice", exact=True).click()
    visitor.get_by_role("button", name="Refresh", exact=True).click()
    expect(visitor.locator("#owner-notice")).to_be_hidden()
    assert "announce" not in app.get(url).headers


def test_visitors_saved_language_outlasts_the_owners_person_default(owner: Page, visitor: Page) -> None:
    person(owner)
    language = editor(owner, "language")
    language.get_by_role("combobox").select_option("ru")
    language.get_by_role("button", name="Save", exact=True).click()
    expect(language).not_to_be_visible()
    url = share_url(owner)
    visitor.goto(url)
    expect(visitor.locator("html")).to_have_attribute("lang", "ru")
    expect(visitor.locator(".account-status")).to_have_text("Активен")
    visitor.locator('[data-action="lang"][data-lang="en"]').click()
    expect(visitor.locator("html")).to_have_attribute("lang", "en")
    visitor.reload()
    expect(visitor.locator("html")).to_have_attribute("lang", "en")
    expect(visitor.locator(".account-status")).to_have_text("Active")
    owner.reload()
    expect(owner.locator('[data-person-field="language"]')).to_contain_text("Русский")
