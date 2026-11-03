"""Drafts, reviews, recoverable refusals and focus work in real browser history and layout."""

import re

import pytest
from playwright.sync_api import Page, expect

from meridian.control.owner import CONFIRM_SECONDS
from meridian.control.service import ControlService
from meridian.core.people_bulk import BulkUndoRequest
from tests.e2e.helpers import editor, person
from tests.e2e.support import OWNER_PASSWORD


@pytest.mark.parametrize("destination", ["tab", "route", "back"], ids=["person-tab", "other-screen", "browser-back"])
def test_stay_keeps_the_draft_and_focus_then_leave_discards_without_a_write(
    owner: Page, service: ControlService, destination: str
) -> None:
    person(owner)
    before = service.get_user("Luma")
    form = editor(owner, "note")
    note = form.get_by_role("textbox")
    note.fill("An unfinished private draft")

    def leave() -> None:
        if destination == "tab":
            owner.get_by_role("tab", name="Usage", exact=True).click()
        elif destination == "route":
            owner.get_by_role("navigation").get_by_role("link", name="Settings", exact=True).click()
        else:
            owner.go_back(wait_until="commit")

    leave()
    dialog = owner.get_by_role("dialog", name="Unsaved edits", exact=True)
    expect(dialog).to_be_visible()
    dialog.get_by_role("button", name="Stay", exact=True).click()
    expect(note).to_have_value("An unfinished private draft")
    expect(note).to_be_focused()
    expect(owner).to_have_url(re.compile(r"#/people/Luma$"))
    assert service.get_user("Luma") == before
    leave()
    dialog.get_by_role("button", name="Leave", exact=True).click()
    expect(form).not_to_be_visible()
    assert service.get_user("Luma") == before
    if destination == "tab":
        owner.get_by_role("tab", name="Access & limits", exact=True).click()
    else:
        person(owner)
    reopened = editor(owner, "note")
    assert before is not None
    expect(reopened.get_by_role("textbox")).to_have_value(before.note)



def test_stay_returns_to_the_edited_field_after_tabbing_past_an_untouched_one(owner: Page) -> None:
    person(owner)
    form = editor(owner, "traffic")
    amount = form.get_by_label("Traffic limit", exact=True)
    amount.fill("75")
    untouched = form.locator("input:focus, select:focus")
    for _ in range(5):
        owner.keyboard.press("Tab")
        if untouched.count() and not amount.evaluate("el => el === document.activeElement"):
            break
    expect(untouched).to_have_count(1)
    owner.get_by_role("navigation").get_by_role("link", name="Settings", exact=True).click()
    owner.get_by_role("dialog", name="Unsaved edits", exact=True).get_by_role("button", name="Stay", exact=True).click()
    expect(amount).to_be_focused()
    expect(amount).to_have_value("75")

def test_simple_and_detailed_keep_an_open_metadata_draft_mounted(owner: Page, service: ControlService) -> None:
    person(owner)
    before = service.get_user("Luma")
    note = editor(owner, "note").get_by_role("textbox")
    note.fill("Keep this draft through mode changes")
    owner.get_by_role("radio", name="Simple", exact=True).click()
    expect(note).to_be_visible()
    expect(note).to_have_value("Keep this draft through mode changes")
    owner.get_by_role("radio", name="Detailed", exact=True).click()
    expect(note).to_be_visible()
    expect(note).to_have_value("Keep this draft through mode changes")
    assert service.get_user("Luma") == before


def test_filter_selection_reviews_commits_and_undoes_only_the_matching_people(
    owner: Page, service: ControlService
) -> None:
    before = {user.username: user.status for user in service.list_users()}
    owner.get_by_role("navigation").get_by_role("link", name="People", exact=True).click()
    owner.locator('[data-filter="tag"] summary').click()
    owner.locator('select[name="tag"]').select_option("FAMILY")
    expect(owner.locator(".people-item")).to_have_count(2)
    owner.get_by_role("checkbox", name="Select this page", exact=True).check()
    owner.locator("[data-select-matching]").click()
    owner.locator('[data-bulk-action="disable"]').click()
    review = owner.get_by_role("dialog")
    expect(review.locator(".bulk-person")).to_have_count(2)
    expect(review).to_contain_text("Luma")
    expect(review).to_contain_text("Tavi")
    assert {user.username: user.status for user in service.list_users()} == before
    review.locator("[data-bulk-commit]").click()
    receipt = owner.get_by_role("dialog", name="Operation receipt", exact=True)
    expect(receipt).to_be_visible()
    for user in service.list_users():
        assert user.status == ("disabled" if user.username in {"Luma", "Tavi"} else before[user.username])
    receipt.locator("[data-bulk-undo]").click()
    expect(receipt).to_contain_text("The changes were undone.")
    expect(receipt.locator("[data-bulk-undo]")).to_be_hidden()
    assert {user.username: user.status for user in service.list_users()} == before
    # The UI hides a used Undo. Replaying its real request must also explain the refusal.
    receipt_text = receipt.inner_text()
    receipt_id = re.search(r"Receipt: ([0-9a-f-]+)", receipt_text)
    assert receipt_id is not None, receipt_text
    undo = BulkUndoRequest(receipt_id=receipt_id[1])
    reply = owner.request.post(
        owner.url.split("/#", 1)[0] + "/api/people/bulk/undo",
        data=undo.model_dump(mode="json"),
        headers={"X-Meridian-Request": "1"},
    )
    assert reply.status == 409
    assert "already undone" in reply.text().lower()


def test_step_up_resumes_the_privacy_change_without_reentering_the_form(
    owner: Page, service: ControlService, clock, browser_faults
) -> None:
    clock.advance(seconds=CONFIRM_SECONDS + 1)
    owner.get_by_role("navigation").get_by_role("link", name="Settings", exact=True).click()
    owner.get_by_role("tab", name="Privacy", exact=True).click()
    owner.locator('[name="device_limits"][value="private"]').check()
    owner.get_by_role("button", name="Save", exact=True).click()
    owner.get_by_role("dialog").get_by_role("button", name="Save", exact=True).click()
    browser_faults.expect(r"console: .*?/api/privacy .*403")
    confirmation = owner.get_by_role("dialog", name="Confirm it's you", exact=True)
    expect(confirmation).to_be_visible()
    assert service.privacy().device_limits == "full"
    confirmation.get_by_label("Password", exact=True).fill(OWNER_PASSWORD)
    confirmation.get_by_role("button", name="Confirm", exact=True).click()
    expect(confirmation).not_to_be_visible()
    expect(owner.locator(".toast").filter(has_text="Privacy")).to_be_visible()
    assert service.privacy().device_limits == "private"
    expect(owner.locator('[name="device_limits"][value="private"]')).to_be_checked()


@pytest.mark.parametrize(
    "refusal", ["invalid_quota", "existing_person"], ids=["invalid-quota", "service-name-conflict"]
)
def test_a_refused_save_keeps_input_and_explains_how_to_recover(
    owner: Page, service: ControlService, refusal: str, browser_faults
) -> None:
    if refusal == "invalid_quota":
        person(owner)
        before = service.get_user("Luma")
        form = editor(owner, "traffic")
        amount = form.get_by_label("Traffic limit", exact=True)
        amount.fill("-12")
        form.get_by_role("button", name="Save", exact=True).click()
        expect(amount).to_have_value("-12")
        expect(amount).to_be_focused()
        expect(form.get_by_role("alert").filter(has_text="Enter a positive amount")).to_be_visible()
        assert service.get_user("Luma") == before
        amount.fill("120")
        form.get_by_role("button", name="Save", exact=True).click()
        expect(form).not_to_be_visible()
        assert service.get_user("Luma").traffic_limit_bytes == 120 * 1024**3
    else:
        owner.get_by_role("button", name="Add person", exact=True).click()
        form = owner.get_by_role("dialog")
        name = form.get_by_label("Name", exact=True)
        name.fill("Luma")
        before = service.get_user("Luma")
        browser_faults.expect(r"console: .*?/api/users .*409")
        form.get_by_role("button", name="Add", exact=True).click()
        expect(form.locator(".form-error")).to_contain_text(re.compile("already|exists", re.I))
        expect(name).to_have_value("Luma")
        assert service.get_user("Luma") == before
        name.fill("E2E_retry")
        form.get_by_role("button", name="Add", exact=True).click()
        expect(owner.get_by_role("heading", name="E2E_retry", exact=True)).to_be_visible()
        assert service.get_user("E2E_retry") is not None


@pytest.mark.parametrize("width", [1440, 390], ids=["desktop-keyboard", "phone-edge"])
def test_keyboard_tip_closes_on_tab_and_escape_and_fits_the_real_viewport(owner: Page, width: int) -> None:
    owner.set_viewport_size({"width": width, "height": 844})
    person(owner)
    topic = owner.locator('[data-person-field="expiry"] .tip')
    trigger = topic.get_by_role("button")
    trigger.focus()
    trigger.press("Enter")
    bubble = topic.get_by_role("status")
    expect(bubble).to_be_visible()
    rect = bubble.bounding_box()
    assert rect is not None
    assert 0 <= rect["x"] and rect["x"] + rect["width"] <= width
    assert 0 <= rect["y"] and rect["y"] + rect["height"] <= 844
    trigger.press("Tab")
    expect(bubble).to_be_hidden()
    trigger.focus()
    trigger.press("Enter")
    expect(bubble).to_be_visible()
    owner.keyboard.press("Escape")
    expect(bubble).to_be_hidden()
    expect(trigger).to_be_focused()
