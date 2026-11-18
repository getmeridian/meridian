"""Owner edits work on the service and the public demo, with a review before the write."""

from playwright.sync_api import Locator, Page, expect

from meridian.control.owner import CONFIRM_SECONDS
from meridian.control.service import ControlService
from meridian.core.presentation import PresentationPatch
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
    interval_row = owner.locator("dt").filter(has_text="Refresh interval (hours)").locator("..")
    expect(interval_row).to_contain_text("6")
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
    expect(interval_row).to_contain_text("19")


def test_stale_edit_keeps_the_draft_and_reload_requires_discard(
    owner: Page, base_url: str, service: ControlService, browser_faults
) -> None:
    owner.get_by_role("navigation").get_by_role("link", name="Settings", exact=True).click()
    original = connection_editor(owner)
    expect(original.get_by_label("Title", exact=True)).to_have_attribute("maxlength", "80")
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
    reload = original.get_by_role("button", name="Load current settings")
    expect(reload).to_be_focused()
    reload.click()
    discard = owner.get_by_role("dialog", name="Load current settings", exact=True)
    discard.get_by_role("button", name="Cancel").click()
    expect(original.get_by_label("Title", exact=True)).to_have_value("My unsaved title")
    reload.click()
    discard.get_by_role("button", name="Load current settings").click()
    expect(original.get_by_label("Title", exact=True)).to_have_value("Current fleet title")
    original.get_by_role("button", name="Review changes").click()
    expect(original).not_to_be_visible()
    expect(owner.locator("dt").filter(has_text="Title").locator("..")).to_contain_text("Current fleet title")


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


def test_phone_billing_edit_keeps_exact_allowance_and_returns_to_the_server(
    owner: Page, service: ControlService
) -> None:
    owner.set_viewport_size({"width": 390, "height": 844})
    state = service.presentation()
    ref = "srv-de"
    # The browser cannot represent this 64-bit byte count exactly.
    large = (1 << 54) + 1
    state = service.update_presentation(
        PresentationPatch.model_validate(
            {"expected_version": state.version, "servers": {ref: {"allowance_bytes": large}}}
        )
    )
    owner.goto(owner.url.split("#", 1)[0] + "#/servers")
    ready(owner, "servers")
    trigger = owner.locator('[data-billing-edit="srv-de"]')
    trigger.click()
    form = owner.get_by_role("dialog", name="Edit allowance and payments")
    expect(form.locator('input[type="date"]')).to_be_enabled()
    form.locator('input[type="date"]').fill("")
    expect(form.get_by_label("Repeat", exact=True)).to_be_disabled()
    review(owner, form).get_by_role("button", name="Save").click()
    expect(form).not_to_be_visible()
    expect(trigger).to_be_focused()
    after = service.presentation()
    assert after.version == state.version + 1
    assert after.servers[ref] == state.servers[ref].model_copy(update={"pay_on": None, "pay_every": None})
    assert after.servers[ref].allowance_bytes == large
