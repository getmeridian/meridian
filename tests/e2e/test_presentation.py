"""Owner edits work on the service and the public demo, with a review before the write."""

from playwright.sync_api import Locator, Page, expect

from meridian.control.owner import CONFIRM_SECONDS
from meridian.control.service import ControlService
from tests.e2e.helpers import ready
from tests.e2e.support import OWNER_PASSWORD


def connection_editor(page: Page) -> Locator:
    page.get_by_role("button", name="Edit connection settings").click()
    dialog = page.get_by_role("dialog", name="Edit connection settings", exact=True)
    expect(dialog.get_by_label("Title", exact=True)).to_be_enabled()
    return dialog


def review(page: Page, form: Locator) -> Locator:
    form.get_by_role("button", name="Review changes").click()
    return page.get_by_role("dialog", name="Review changes", exact=True)


def test_contact_and_refresh_are_reviewed_then_confirmed_with_the_owner(
    owner: Page, service: ControlService, clock, browser_faults
) -> None:
    clock.advance(seconds=CONFIRM_SECONDS + 1)
    owner.get_by_role("navigation").get_by_role("link", name="Settings", exact=True).click()
    form = connection_editor(owner)
    before = service.settings()
    form.get_by_label("Refresh interval (hours)").fill("19")
    form.get_by_label("Contact instructions").fill("Ask the household admin")
    check = review(owner, form)
    expect(check).to_contain_text("Ask the household admin")
    expect(check).to_contain_text("19")
    check.get_by_role("button", name="Cancel").click()
    expect(form.get_by_label("Refresh interval (hours)")).to_have_value("19")
    assert service.settings() == before
    check = review(owner, form)
    browser_faults.expect(r"console: .*?/api/settings/presentation .*403")
    check.get_by_role("button", name="Save", exact=True).click()
    identity = owner.get_by_role("dialog", name="Confirm it's you")
    expect(identity).to_be_visible()
    assert service.settings() == before
    identity.get_by_label("Password", exact=True).fill(OWNER_PASSWORD)
    identity.get_by_role("button", name="Confirm", exact=True).click()
    expect(form).not_to_be_visible()
    assert service.settings().owner_contact.text == "Ask the household admin"
    assert service.settings().update_interval_hours == 19


def test_stale_edit_keeps_the_draft_and_reload_requires_discard(
    owner: Page, base_url: str, service: ControlService, browser_faults
) -> None:
    owner.get_by_role("navigation").get_by_role("link", name="Settings", exact=True).click()
    original = connection_editor(owner)
    original.get_by_label("Title", exact=True).fill("My unsaved title")
    other = owner.context.new_page()
    try:
        other.goto(base_url + "/#/settings")
        second = connection_editor(other)
        second.get_by_label("Title", exact=True).fill("Current fleet title")
        review(other, second).get_by_role("button", name="Save").click()
        expect(second).not_to_be_visible()
    finally:
        other.close()
    browser_faults.expect(r"console: .*?/api/settings/presentation .*409")
    review(owner, original).get_by_role("button", name="Save").click()
    expect(original.get_by_role("alert")).to_contain_text("Someone changed these settings")
    expect(original.get_by_label("Title", exact=True)).to_have_value("My unsaved title")
    assert service.settings().profile_title == "Current fleet title"
    original.get_by_role("button", name="Load current settings").click()
    discard = owner.get_by_role("dialog", name="Load current settings", exact=True)
    discard.get_by_role("button", name="Load current settings").click()
    expect(original.get_by_label("Title", exact=True)).to_have_value("Current fleet title")


def test_demo_billing_edit_reaches_server_card_and_reload_resets_it(visitor: Page, demo_url: str) -> None:
    visitor.goto(demo_url + "/console/?lang=en#/servers")
    ready(visitor, "servers")
    details = visitor.locator('[data-mode-section="allowance"]').first
    details.locator("summary").click()
    details.get_by_role("button", name="Edit allowance and payments").click()
    form = visitor.get_by_role("dialog", name="Edit allowance and payments")
    provider = form.get_by_label("Provider", exact=True)
    expect(provider).to_be_enabled()
    before = provider.input_value()
    provider.fill("Weekend hosting")
    form.get_by_label("Payment anchor", exact=True).fill("2027-03-12")
    review(visitor, form).get_by_role("button", name="Save").click()
    expect(form).not_to_be_visible()
    expect(details).to_contain_text("Weekend hosting")
    expect(details).to_contain_text("2027-03-12")
    visitor.reload()
    ready(visitor, "servers")
    details.locator("summary").click()
    expect(details).to_contain_text(before)
    expect(details).not_to_contain_text("Weekend hosting")
