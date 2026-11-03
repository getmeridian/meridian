"""Browser preferences, mirrored phone layout and assistive/keyboard navigation."""

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.helpers import LOCALES, SCREENS, editor, language, loaded, person, ready


def test_saved_modes_and_detailed_bookmarks_keep_each_destination(owner: Page, base_url: str) -> None:
    # Read-only bookmarks share one service/context; each destination still checks both reloads.
    for route in (
        "revisions",
        "tokens",
        "people/Luma/usage",
        "people/Luma/devices",
        "people/Luma/routing",
        "people/Luma/keys",
    ):
        owner.get_by_role("radio", name="Simple", exact=True).click()
        owner.reload()
        expect(owner.get_by_role("radio", name="Simple", exact=True)).to_have_attribute("aria-checked", "true")
        owner.goto(base_url + "/#/" + route)
        ready(owner, route)
        note = owner.locator(".mode-note:visible")
        expect(note).to_contain_text("Detailed")
        note.get_by_role("button", name="Switch to Detailed", exact=True).click()
        expect(owner.locator("html")).to_have_attribute("data-mode", "detailed")
        expect(note).to_have_count(0)
        expect(owner).to_have_url(base_url + "/#/" + route)
        owner.reload()
        ready(owner, route)
        expect(owner.get_by_role("radio", name="Detailed", exact=True)).to_have_attribute("aria-checked", "true")
        if route.startswith("people/"):
            expect(owner.locator(f'[data-person-tab="{route.split("/")[-1]}"]')).to_have_attribute(
                "aria-selected", "true"
            )


def test_language_guard_keeps_draft_then_persists_and_mirrors_persian(owner: Page) -> None:
    person(owner)
    note = editor(owner, "note").get_by_role("textbox")
    note.fill("Keep the language draft")
    owner.locator(".account-toggle").click()
    owner.locator("[data-console-language]").select_option("fa")
    dialog = owner.get_by_role("dialog", name="Unsaved edits", exact=True)
    dialog.get_by_role("button", name="Stay", exact=True).click()
    expect(note).to_have_value("Keep the language draft")
    expect(note).to_be_focused()
    expect(owner.locator("html")).to_have_attribute("lang", "en")
    expect(owner.locator("[data-console-language]")).to_have_value("en")
    owner.locator(".account-toggle").click()
    owner.locator("[data-console-language]").select_option("fa")
    dialog.get_by_role("button", name="Leave", exact=True).click()
    expect(owner.locator("html")).to_have_attribute("lang", "fa")
    owner.reload()
    ready(owner, "people/Luma")
    expect(owner.locator("html")).to_have_attribute("lang", "fa")
    expect(owner.locator("html")).to_have_attribute("dir", "rtl")
    nav = owner.locator(".tabs")
    first, last = nav.locator("a").first.bounding_box(), nav.locator("a").last.bounding_box()
    assert first and last and first["x"] > last["x"]
    actions, title = (
        owner.locator(".person-primary-actions").bounding_box(),
        owner.locator(".person-title-row").bounding_box(),
    )
    assert actions and title and actions["x"] + actions["width"] <= title["x"]
    owner.locator('[data-person-edit="note"]').click()
    expect(owner.locator('[data-person-field="note"] textarea')).not_to_have_value("Keep the language draft")


@pytest.mark.parametrize("locale", LOCALES)
def test_phone_screens_fit_both_themes_in_each_locale(owner: Page, base_url: str, locale: str) -> None:
    # One isolated service/context per locale, ten layouts per case; no reseed per theme/screen.
    owner.set_viewport_size({"width": 390, "height": 844})
    if locale != "en":
        language(owner, locale)
    for theme in ("light", "dark"):
        owner.locator(".account-toggle").click()
        owner.locator(f'[data-theme-choice="{theme}"]').click()
        owner.keyboard.press("Escape")
        for route in SCREENS:
            owner.goto(base_url + "/#/" + route)
            ready(owner, route)
            expect(owner.locator("html")).to_have_attribute("lang", locale)
            expect(owner.locator("html")).to_have_attribute("data-theme", theme)
            # innerWidth would count the scrollbar and hide that much overflow.
            fits = "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
            assert owner.evaluate(fits), (locale, theme, route)


def accessible_controls(page: Page) -> None:
    root = page.get_by_role("dialog").last if page.get_by_role("dialog").count() else page.locator("body")
    controls = root.locator(
        "button:visible, a[href]:visible, input:visible, select:visible, textarea:visible, summary:visible"
    )
    assert controls.count() > 0
    client = page.context.new_cdp_session(page)
    selector = "button, a[href], input, select, textarea, summary"

    def snapshot() -> tuple[list[int], list[int], dict[int, int], dict[int, dict]] | None:
        document = client.send("DOM.getDocument", {"depth": -1})["root"]
        backend_ids: dict[int, int] = {}

        def index_nodes(node: dict) -> None:
            backend_ids[node["nodeId"]] = node["backendNodeId"]
            for child in node.get("children", []):
                index_nodes(child)

        index_nodes(document)
        node_ids = client.send("DOM.querySelectorAll", {"nodeId": document["nodeId"], "selector": selector})["nodeIds"]
        visible = controls.evaluate_all(
            """(els, selector) => {
                const all = [...document.querySelectorAll(selector)];
                return {total: all.length, indices: els.map(el => all.indexOf(el))};
            }""",
            selector,
        )
        # Playwright's Chromium accessibility session reports computed names, including native summaries.
        tree = client.send("Accessibility.getFullAXTree")["nodes"]
        after = page.evaluate("selector => document.querySelectorAll(selector).length", selector)
        # The reads are separate round trips; a late render between them would pair the wrong nodes.
        if not len(node_ids) == visible["total"] == after or any(node not in backend_ids for node in node_ids):
            return None
        names = {node["backendDOMNodeId"]: node for node in tree if "backendDOMNodeId" in node}
        return visible["indices"], node_ids, backend_ids, names

    try:
        for _ in range(3):
            read = snapshot()
            if read:
                break
        else:
            pytest.fail("The page kept changing while its accessibility tree was read")
        visible, node_ids, backend_ids, names = read
        for position, index in enumerate(visible):
            accessible = names.get(backend_ids[node_ids[index]], {})
            assert not accessible.get("ignored", True) and accessible.get("name", {}).get("value", "").strip(), (
                controls.nth(position).aria_snapshot()
            )
    finally:
        client.detach()
    for container in page.locator("dialog:visible").all() + root.locator('[role="tabpanel"]:visible').all():
        labelled_by = container.get_attribute("aria-labelledby")
        assert labelled_by
        label = page.locator(f'[id="{labelled_by}"]')
        expect(label).to_be_visible()
        assert label.inner_text().strip()
        if container.get_attribute("role") == "tabpanel":
            expect(label).to_have_attribute("role", "tab")
            expect(label).to_have_attribute("aria-controls", container.get_attribute("id") or "")


def test_accessible_names_across_screens_tabs_editors_and_dialogs(owner: Page, base_url: str) -> None:
    for route in [
        *SCREENS,
        "revisions",
        "tokens",
        "settings/notice",
        "settings/language",
        "settings/privacy",
        "settings/security",
        "settings/advanced",
    ]:
        owner.goto(base_url + "/#/" + route)
        ready(owner, route)
        accessible_controls(owner)
        if route == "overview":
            owner.locator(".account-toggle").click()
            accessible_controls(owner)
            owner.keyboard.press("Escape")
    person(owner)
    for tab in ["access", "usage", "devices", "routing", "keys"]:
        owner.locator(f'[data-person-tab="{tab}"]').click()
        expect(owner.locator(f'[data-person-panel="{tab}"]')).to_be_visible()
        loaded(owner, tab)
        accessible_controls(owner)
    owner.locator('[data-person-tab="access"]').click()
    for field in ("note", "traffic"):
        form = editor(owner, field)
        accessible_controls(owner)
        form.get_by_role("button", name="Cancel", exact=True).click()
        expect(form).not_to_be_visible()
    owner.locator('[data-person-action="share"]').click()
    expect(owner.get_by_role("dialog").get_by_role("link", name="Open page", exact=True)).to_be_visible()
    accessible_controls(owner)
    owner.keyboard.press("Escape")
    owner.get_by_role("navigation").get_by_role("link", name="Overview", exact=True).click()
    owner.get_by_role("button", name="Add person", exact=True).click()
    accessible_controls(owner)
