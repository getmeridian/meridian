"""The exported demo runs real console and connection-page assets together."""

import re
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.helpers import LOCALES, add_person, language, person, ready


@pytest.fixture
def demo(visitor: Page, demo_url: str) -> Page:
    visitor.goto(demo_url + "/console/?lang=en#/overview")
    visitor.get_by_role("radio", name="Detailed", exact=True).click()
    ready(visitor, "overview")
    return visitor


@pytest.mark.parametrize("locale", LOCALES)
def test_every_demo_screen_and_person_tab_loads_without_browser_faults(demo: Page, locale: str) -> None:
    if locale != "en":
        language(demo, locale)
    for route in ["overview", "people", "servers", "revisions", "tokens", "settings"]:
        demo.locator(f'.tabs a[href="#/{route}"]').click()
        ready(demo, route)
        expect(demo.locator("html")).to_have_attribute("lang", locale)
    demo.locator('.tabs a[href="#/people"]').click()
    demo.locator('.people-row[href="#/people/Luma"]').click()
    for tab in ["access", "usage", "devices", "routing", "keys"]:
        demo.locator(f'[data-person-tab="{tab}"]').click()
        expect(demo.locator(f'[data-person-panel="{tab}"]')).to_be_visible()
        expect(demo.locator("#view .spinner:visible")).to_have_count(0)
        expect(demo.locator("html")).to_have_attribute("lang", locale)
    demo.locator('.tabs a[href="#/revisions"]').click()
    demo.locator('a.record-main[href^="#/revisions/"]').first.click()
    expect(demo.locator("#view .spinner:visible")).to_have_count(0)
    demo.locator('.tabs a[href="#/settings"]').click()
    for section in ["general", "notice", "language", "privacy", "security", "advanced"]:
        demo.locator(f'[data-settings-section="{section}"]').click()
        expect(demo.locator(f'[data-settings-panel="{section}"]')).to_be_visible()
        expect(demo.locator("#view .spinner:visible")).to_have_count(0)
        expect(demo.locator("html")).to_have_attribute("lang", locale)


def chosen_language(page: Page, code: str) -> None:
    expect(page.locator("html")).to_have_attribute("lang", code)
    expect(page).to_have_url(re.compile(r"[?&]lang=" + code + r"(?:&|#|$)"))
    assert parse_qs(urlsplit(page.url).query)["lang"] == [code]


def test_demo_language_url_survives_reload_navigation_and_cross_surface_links(demo: Page) -> None:
    language(demo, "ru")
    chosen_language(demo, "ru")
    demo.reload()
    chosen_language(demo, "ru")
    demo.locator('.tabs a[href="#/people"]').click()
    ready(demo, "people")
    chosen_language(demo, "ru")
    demo.locator("#demo-banner a").click()
    expect(demo.locator("#account-panel")).to_have_attribute("aria-busy", "false")
    chosen_language(demo, "ru")
    demo.locator('[data-action="lang"][data-lang="fa"]').click()
    chosen_language(demo, "fa")
    demo.reload()
    chosen_language(demo, "fa")
    expect(demo.locator("#account-panel")).to_have_attribute("aria-busy", "false")
    demo.locator("#demo-banner a").click()
    ready(demo, "overview")
    chosen_language(demo, "fa")
    demo.locator('.tabs a[href="#/settings"]').click()
    ready(demo, "settings")
    chosen_language(demo, "fa")


def test_demo_add_disable_toast_undo_and_bulk_receipt_undo(demo: Page) -> None:
    add_person(demo, "Demo_visitor")
    demo.get_by_role("button", name="Disable", exact=True).click()
    expect(demo.get_by_role("button", name="Enable", exact=True)).to_be_enabled()
    demo.locator(".toast").get_by_role("button", name="Undo", exact=True).click()
    expect(demo.get_by_role("button", name="Disable", exact=True)).to_be_enabled()
    demo.locator('.tabs a[href="#/people"]').click()
    demo.locator('[data-filter="tag"] summary').click()
    demo.locator('select[name="tag"]').select_option("FAMILY")
    expect(demo.locator(".people-item")).to_have_count(2)
    demo.get_by_role("checkbox", name="Select this page", exact=True).check()
    demo.locator('[data-bulk-action="disable"]').click()
    review = demo.get_by_role("dialog")
    expect(review.locator(".bulk-person")).to_have_count(2)
    review.locator("[data-bulk-commit]").click()
    receipt = demo.get_by_role("dialog", name="Operation receipt", exact=True)
    expect(receipt).to_be_visible()
    expect(receipt.locator(".bulk-person")).to_have_count(2)
    for row in receipt.locator(".bulk-person").all():
        expect(row.locator(".bulk-changes")).to_contain_text("Active")
        expect(row.locator(".bulk-changes")).to_contain_text("Disabled")
    receipt.locator("[data-bulk-undo]").click()
    expect(receipt).to_contain_text("The changes were undone.")
    receipt.locator(".dialog-footer").get_by_role("button", name="Close", exact=True).click()
    demo.locator('.people-row[href="#/people/Luma"]').click()
    expect(demo.get_by_role("button", name="Disable", exact=True)).to_be_enabled()
    demo.locator('.tabs a[href="#/people"]').click()
    demo.locator('.people-row[href="#/people/Tavi"]').click()
    expect(demo.get_by_role("button", name="Disable", exact=True)).to_be_enabled()
    demo.reload()
    demo.locator('.tabs a[href="#/people"]').click()
    expect(demo.locator('.people-row[href="#/people/Demo_visitor"]')).to_have_count(0)


def test_demo_unavailable_remote_actions_explain_the_refusal(demo: Page) -> None:
    demo.locator('.tabs a[href="#/servers"]').click()
    ready(demo, "servers")
    server = demo.locator("[data-server]").first
    server.locator(".secondary-actions > summary").click()
    server.get_by_role("button", name="Restart Xray", exact=True).click()
    expect(demo.locator("#toast.show .toast")).to_have_text("This action is unavailable in the demo.")
    demo.reload()
    expect(demo.locator("#toast.show")).to_have_count(0)
    demo.locator('.tabs a[href="#/settings"]').click()
    demo.locator('[data-settings-section="security"]').click()
    demo.get_by_role("button", name="Turn on", exact=True).click()
    expect(demo.locator("#toast.show .toast")).to_have_text("This action is unavailable in the demo.")
    expect(demo.get_by_role("dialog")).to_have_count(0)


def test_demo_share_opens_that_persons_connection_page_and_switches_language(demo: Page, demo_url: str) -> None:
    person(demo)
    demo.locator('[data-person-action="share"]').click()
    dialog = demo.get_by_role("dialog")
    link = dialog.get_by_role("link", name="Open page", exact=True)
    expect(link).to_have_attribute("href", re.compile(re.escape(demo_url) + r"/sub/[^/]+/?\?lang=en"))
    with demo.expect_popup() as opened:
        link.click()
    page = opened.value
    expect(page.locator('[data-t="hero.for"]')).to_contain_text("Luma")
    expect(page.locator("#account-panel")).to_have_attribute("aria-busy", "false")
    expect(page.locator(".account-status")).to_have_text("Active")
    page.locator("#connections-panel > summary").click()
    expect(page.locator(".conn").first).to_be_visible()
    page.locator('[data-action="lang"][data-lang="zh"]').click()
    chosen_language(page, "zh")
    page.reload()
    chosen_language(page, "zh")
    page.locator("#connections-panel > summary").click()
    expect(page.locator(".conn").first).to_be_visible()
