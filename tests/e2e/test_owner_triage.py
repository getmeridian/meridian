"""An owner follows health and billing findings to their respective controls."""

from axe_playwright_python.sync_playwright import Axe
from playwright.sync_api import Page, expect

from tests.e2e.helpers import ready


def test_overview_separates_operations_billing_and_routine_states(owner: Page, base_url: str) -> None:
    overview = base_url + "/#/overview"
    owner.goto(overview)
    ready(owner, "overview")
    queue = owner.locator(".overview-attention")
    routine = queue.locator('a[href="#/people?status=disabled"]')
    expect(routine).to_be_hidden()
    queue.locator("summary").click()
    expect(routine).to_be_visible()

    runtime = queue.locator('a[href="#/servers/de"]')
    runtime.click()
    server = owner.locator('[data-server="de"]')
    expect(server).to_be_focused()

    owner.goto(overview)
    ready(owner, "overview")
    queue.locator('a[href="#/servers/de/allowance"]').click()
    allowance = server.locator('[data-mode-section="allowance"]')
    expect(allowance.locator("summary")).to_be_focused()
    expect(allowance.locator("[data-billing-edit]")).to_be_visible()


def test_phone_filters_keep_search_and_current_choices_in_reach(owner: Page, base_url: str) -> None:
    owner.set_viewport_size({"width": 390, "height": 844})
    owner.goto(base_url + "/#/people?status=active")
    ready(owner, "people")
    toggle = owner.locator(".people-filter-toggle")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(owner.locator("[data-remove-filter=status]")).to_be_visible()
    toggle.click()
    expect(owner.locator('[data-filter="sort"] > summary')).to_be_visible()
    toggle.click()
    owner.locator("[data-remove-filter=status]").click()
    expect(toggle).to_be_focused()
    expect(owner.locator(".people-row").first).to_be_visible()


def test_owner_can_read_daily_values_without_the_chart_in_either_direction(owner: Page, base_url: str) -> None:
    for locale, theme, width in (("en", "light", 1280), ("fa", "dark", 390)):
        owner.set_viewport_size({"width": width, "height": 844})
        owner.evaluate(
            "([lang, theme]) => { localStorage.setItem('mrd.lang', lang); localStorage.setItem('mrd.theme', theme) }",
            [locale, theme],
        )
        owner.goto(base_url + "/#/overview")
        owner.reload()
        ready(owner, "overview")
        daily = owner.locator(".overview-traffic")
        daily.locator("summary").focus()
        owner.keyboard.press("Enter")
        expect(daily.get_by_role("table")).to_be_visible()
        assert daily.locator("tbody tr").count() == 30
        review = Axe().run(
            owner, options={"runOnly": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"], "resultTypes": ["violations"]}
        )
        assert not review.response["violations"], review.generate_report()
