"""Native keyboard focus survives creation, sharing and modal dismissal."""

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.e2e.helpers import ready


def focus_visible(page: Page) -> None:
    expect(page.locator(":focus")).to_be_visible()
    assert page.locator(":focus").evaluate("""el => {
        const s = getComputedStyle(el);
        return (parseFloat(s.outlineWidth) > 0 && !['none', 'hidden'].includes(s.outlineStyle))
            || s.boxShadow !== 'none';
    }"""), page.locator(":focus").evaluate("el => el.outerHTML.slice(0, 400)")


def tab_to(page: Page, target: Locator, *, backwards: bool = False) -> None:
    expect(target).to_be_enabled()
    for _ in range(80):
        if target.evaluate("el => el === document.activeElement"):
            focus_visible(page)
            return
        page.keyboard.press("Shift+Tab" if backwards else "Tab")
        focus_visible(page)
    pytest.fail("Keyboard could not reach " + target.aria_snapshot())


def test_keyboard_add_share_and_dialog_focus_return(owner: Page) -> None:
    ready(owner, "overview")
    add = owner.get_by_role("button", name="Add person", exact=True)
    tab_to(owner, add)
    owner.keyboard.press("Enter")
    dialog = owner.get_by_role("dialog", name="Add a person", exact=True)
    expect(dialog).to_be_visible()
    focus_visible(owner)
    owner.keyboard.press("Escape")
    expect(dialog).not_to_be_visible()
    expect(add).to_be_focused()
    focus_visible(owner)
    owner.keyboard.press("Space")
    name = dialog.get_by_role("textbox", name="Name", exact=True)
    expect(name).to_be_focused()
    owner.keyboard.type("Keyboard_visitor")
    focus_visible(owner)
    tab_to(owner, dialog.get_by_role("button", name="Add", exact=True))
    owner.keyboard.press("Enter")
    expect(dialog).not_to_be_visible()
    expect(owner.get_by_role("heading", name="Keyboard_visitor", exact=True)).to_be_visible()
    expect(owner.locator("#view")).to_be_focused()
    focus_visible(owner)
    share = owner.locator("[data-person-action=share]")
    tab_to(owner, share)
    owner.keyboard.press("Enter")
    share_dialog = owner.get_by_role("dialog", name="Share · Keyboard_visitor", exact=True)
    expect(share_dialog).to_be_visible()
    expect(share_dialog.get_by_role("link", name="Open page", exact=True)).to_be_visible()
    focus_visible(owner)
    tab_to(owner, share_dialog.get_by_role("link", name="Open page", exact=True))
    tab_to(owner, share_dialog.get_by_role("button", name="Close", exact=True), backwards=True)
    owner.keyboard.press("Escape")
    expect(share_dialog).not_to_be_visible()
    expect(share).to_be_focused()
    focus_visible(owner)
    tab_to(owner, owner.locator('[data-person-tab="access"]'))
    owner.keyboard.press("ArrowRight")
    expect(owner.locator('[data-person-tab="usage"]')).to_be_focused()
    expect(owner.locator('[data-person-panel="usage"]')).to_be_visible()
    focus_visible(owner)
    owner.keyboard.press("ArrowLeft")
    expect(owner.locator('[data-person-tab="access"]')).to_be_focused()
    focus_visible(owner)
