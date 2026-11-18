"""An owner follows health and billing findings to their respective controls."""

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
